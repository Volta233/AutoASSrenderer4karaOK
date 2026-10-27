"""音频 + 背景图素材：画布推算、上传契约与合成结果。"""

import os
from pathlib import Path

from fastapi.testclient import TestClient
import pytest

from ktv_app.backend.app import create_app, requested_canvas
from ktv_app.backend.service import Projects
from ktv_app.backend.tasks import media as media_tasks
from ktv_app.config import Settings
from ktv_app.media import even, probe, run, still_canvas, still_video

live = pytest.mark.skipif(
    os.getenv("KTV_LIVE_TESTS") != "1", reason="需要真实 FFmpeg"
)


def audio_image(client, audio=b"ID3audio", background=b"PNGimage", **data):
    return client.post(
        "/api/upload",
        files={
            "audio": ("演唱.mp3", audio, "audio/mpeg"),
            "background": ("bg.png", background, "image/png"),
        },
        data=data,
    )


def test_canvas_follows_the_picture_and_stays_even():
    assert still_canvas(1920, 1080) == (1920, 1080)
    assert still_canvas(3840, 2160) == (1920, 1080)
    assert still_canvas(900, 1400) == (900, 1400)
    assert still_canvas(3000, 1000) == (1920, 640)
    assert still_canvas(200, 120) == (600, 360)
    for width, height in ((1001, 777), (1919, 1081), (123, 45)):
        assert all(value % 2 == 0 for value in still_canvas(width, height))
    assert even(7) == 6 and even(0) == 2


def test_canvas_request_accepts_only_even_dimensions_in_range():
    assert requested_canvas("") is None
    assert requested_canvas("  ") is None
    assert requested_canvas("1920x1080") == (1920, 1080)
    assert requested_canvas("1080X1920") == (1080, 1920)
    for value in ("1920", "1920x", "axb", "1919x1080", "100x100", "9999x1080"):
        with pytest.raises(ValueError):
            requested_canvas(value)


def test_upload_creates_an_audio_project_that_owns_its_derived_video(tmp_path):
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        response = audio_image(client, song="晴天", artist="周杰伦", canvas="1920x1080")
        assert response.status_code == 200
        project = response.json()
        folder = tmp_path / "projects" / project["id"]
        source = project["media_source"]
        assert source["kind"] == "audio_image"
        assert source["canvas"] == [1920, 1080]
        assert source["video"] == "video.mp4"
        assert Path(source["audio"]).is_file()
        assert Path(source["background"]).is_file()
        # The derived video lives inside the project so playback and export agree.
        assert Path(project["video"]) == folder / "video.mp4"
        assert not Path(project["video"]).exists()
        assert project["video_name"] == "演唱.mp3"
        # Nothing is extracted until the media step actually runs.
        assert not (folder / "media.json").exists()
        assert project["workflow"]["steps"][0]["complete"] is False


def test_video_upload_keeps_the_plain_source_kind(tmp_path):
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        response = client.post(
            "/api/upload",
            files={"video": ("mv.mp4", b"video bytes", "video/mp4")},
            data={"song": "歌"},
        )
        assert response.status_code == 200
        assert response.json()["media_source"] == {"kind": "video"}


def test_half_a_pair_and_unsupported_types_are_rejected_without_leftovers(tmp_path):
    app = create_app(Settings(projects=tmp_path / "projects"))
    uploads = tmp_path / "projects" / "_uploads"
    with TestClient(app) as client:
        only_audio = client.post(
            "/api/upload", files={"audio": ("a.mp3", b"ID3", "audio/mpeg")}
        )
        assert only_audio.status_code == 400
        assert "一起提供" in only_audio.json()["detail"]
        wrong_audio = client.post(
            "/api/upload",
            files={
                "audio": ("a.txt", b"text", "text/plain"),
                "background": ("bg.png", b"PNG", "image/png"),
            },
        )
        assert wrong_audio.status_code == 400
        bad_canvas = audio_image(client, canvas="1919x1080")
        assert bad_canvas.status_code == 400
        assert not uploads.exists() or not list(uploads.iterdir())


def test_video_projects_never_touch_the_still_image_path(tmp_path, monkeypatch):
    store = Projects(Settings(projects=tmp_path / "projects"))
    try:
        video = tmp_path / "mv.mp4"
        video.write_bytes(b"not passed to a model")
        project = store.create(video, "Song", "Artist", "en")
        store.pool.shutdown(wait=True)

        def refuse(*args, **kwargs):
            raise AssertionError("视频项目不应合成静帧视频")

        monkeypatch.setattr(media_tasks, "still_video", refuse)
        context = type(
            "Context",
            (),
            {
                "project": store.get(project["id"]),
                "folder": store.folder(project["id"]),
                "pid": project["id"],
            },
        )()
        media_tasks.ensure_source_video(store, context)
    finally:
        store.pool.shutdown(wait=False)


@live
def test_still_video_matches_the_audio_length_and_canvas(tmp_path):
    settings = Settings()
    audio = tmp_path / "tone.mp3"
    image = tmp_path / "bg.png"
    run(
        [
            settings.tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=44100:duration=4",
            "-c:a",
            "libmp3lame",
            audio,
        ]
    )
    run(
        [
            settings.tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x3355aa:s=640x360",
            "-frames:v",
            "1",
            image,
        ]
    )
    target = tmp_path / "video.mp4"
    still_video(audio, image, target, settings, 640, 360)
    info = probe(target, settings)
    assert info["width"] == 640 and info["height"] == 360
    assert info["duration"] == pytest.approx(4, abs=0.2)
    assert info["audio_codec"] == "aac"
    assert (tmp_path / "video.log").exists()
