import os
import time

import pytest
from fastapi.testclient import TestClient

from ktv_app.config import Settings
from ktv_app.media import run
from ktv_app.backend.app import create_app

pytestmark = pytest.mark.skipif(
    os.getenv("KTV_LIVE_TESTS") != "1", reason="实际 FFmpeg 与本机网络失败测试"
)


def test_network_failure_can_continue_with_manual_lyrics_and_cache(
    tmp_path, monkeypatch
):
    # Exercise a real refused localhost lyric request. External web search is
    # unrelated to recovery/cache behavior and can outlast this test's deadline.
    monkeypatch.setattr("ktv_app.lyrics.search_web", lambda query: [])
    s = Settings(
        projects=tmp_path / "projects",
        lyrics_url="http://127.0.0.1:1/api",
    )
    video = tmp_path / "input.mp4"
    run(
        [
            s.tool("ffmpeg"),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=25:d=2",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-shortest",
            video,
        ]
    )
    app = create_app(s)
    with TestClient(app) as c:
        p = c.post(
            "/api/projects", json={"video": str(video), "song": "test song"}
        ).json()
        pid = p["id"]
        assert (
            c.post(f"/api/projects/{pid}/tasks", json={"action": "media"}).status_code
            == 200
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            p = c.get("/api/projects/" + pid).json()
            if not p["busy"]:
                break
            time.sleep(0.05)
        assert not p["busy"] and p["stage"] == "media_ready" and not p["error"]
        wav = s.projects / pid / "audio.wav"
        old_time = wav.stat().st_mtime_ns
        assert (
            c.post(f"/api/projects/{pid}/tasks", json={"action": "search"}).status_code
            == 200
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            p = c.get("/api/projects/" + pid).json()
            if not p["busy"]:
                break
            time.sleep(0.05)
        assert not p["busy"] and p["stage"] == "choose_lyrics" and not p["error"]
        assert p["candidates"]["verification"]["retrieval_warning"]
        assert p["candidates"]["candidates"] == []
        p = c.post(f"/api/projects/{pid}/lyrics", json={"text": "Hello world"}).json()
        assert p["stage"] == "lyrics_ready" and not p["error"]
        assert wav.stat().st_mtime_ns == old_time
        assert (
            c.post(f"/api/projects/{pid}/tasks", json={"action": "media"}).status_code
            == 200
        )
        deadline = time.time() + 15
        while time.time() < deadline:
            p = c.get("/api/projects/" + pid).json()
            if not p["busy"]:
                break
            time.sleep(0.05)
        assert not p["error"] and wav.stat().st_mtime_ns == old_time
    app.state.projects.pool.shutdown(wait=True)
