"""Bilibili input validation, task failure/retry and shared media handoff."""

from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ktv_app.bilibili import parse_link, download_stream, download_video, api_data
from ktv_app.backend.app import create_app
from ktv_app.config import Settings
from ktv_app.backend.storage import write_json


@pytest.mark.parametrize(
    "value,page",
    [
        ("BV18i421e7cJ", 1),
        (" https://www.bilibili.com/video/BV18i421e7cJ/?p=2&share_source=copy ", 2),
    ],
)
def test_parse_link(value, page):
    assert parse_link(value) == ("BV18i421e7cJ", page)


@pytest.mark.parametrize(
    "value",
    [
        "",
        "BV123",
        "https://evil.test/video/BV18i421e7cJ",
        "https://www.bilibili.com.evil.test/video/BV18i421e7cJ",
        "https://www.bilibili.com/video/BV18i421e7cJ/?p=0",
        "https://www.bilibili.com/video/BV18i421e7cJ/?p=abc",
        "file:///video/BV18i421e7cJ",
        "https://user@www.bilibili.com/video/BV18i421e7cJ",
    ],
)
def test_reject_invalid_link(value):
    with pytest.raises(ValueError):
        parse_link(value)


def test_download_rejects_untrusted_hosts_and_tries_backup(tmp_path):
    calls = []

    def respond(request):
        calls.append(str(request.url))
        return httpx.Response(
            503 if request.url.path == "/bad" else 200, content=b"media"
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        size = download_stream(
            client,
            [
                "https://localhost/private",
                "https://cdn.bilivideo.com/bad",
                "https://cdn.bilivideo.com/good",
            ],
            tmp_path / "media",
            100,
            lambda message: None,
        )
    assert size == 5
    assert len(calls) == 2
    assert (tmp_path / "media").read_bytes() == b"media"


def test_download_does_not_follow_redirect_or_exceed_limit(tmp_path):
    for response in (
        httpx.Response(302, headers={"location": "http://127.0.0.1/private"}),
        httpx.Response(200, content=b"too big"),
    ):
        with httpx.Client(transport=httpx.MockTransport(lambda r: response)) as client:
            with pytest.raises(ValueError):
                download_stream(
                    client,
                    ["https://cdn.bilivideo.com/file"],
                    tmp_path / "media",
                    3,
                    lambda message: None,
                )


def test_bilibili_access_limit_has_actionable_error():
    with httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(412))
    ) as client:
        with pytest.raises(ValueError, match="HTTP 412.*重新准备"):
            api_data(client, "x/web-interface/view", bvid="BV18i421e7cJ")


def test_api_background_retry_and_normal_lyrics_flow(tmp_path, monkeypatch):
    app = create_app(Settings(projects=tmp_path / "projects"))
    store = app.state.projects
    attempts = []

    def download(source, folder, settings, progress):
        attempts.append(source)
        if len(attempts) == 1:
            raise ValueError("下载中断")
        progress("下载完成")
        (folder / "video.mp4").write_bytes(b"downloaded")

    def extract(video, folder, settings):
        assert Path(video).read_bytes() == b"downloaded"
        info = {"duration": 60, "width": 1280, "height": 720}
        write_json(folder / "media.json", info)
        return info

    monkeypatch.setattr("ktv_app.backend.tasks.media.download_video", download)
    monkeypatch.setattr("ktv_app.backend.tasks.extract", extract)
    with TestClient(app) as client:
        assert client.post("/api/bilibili", json={"url": "wrong"}).status_code == 400
        p = client.post(
            "/api/bilibili", json={"url": "BV18i421e7cJ", "song": "歌"}
        ).json()
        pid = p["id"]
        assert not p["workflow"]["steps"][1]["available"]
        assert p["media_source"]["bvid"] == "BV18i421e7cJ"
        assert not Path(p["video"]).exists()
        # Run the same queued worker synchronously so failure/retry is deterministic.
        monkeypatch.setattr(store.pool, "submit", lambda fn, *args: fn(*args))
        endpoint = f"/api/workflows/{pid}/steps/1"
        failed = client.post(endpoint, json={"action": "media"}).json()
        assert not failed["busy"] and failed["error"] == "下载中断"
        assert not failed["workflow"]["steps"][1]["available"]
        ready = client.post(endpoint, json={"action": "media"}).json()
        assert not ready["busy"] and ready["error"] is None
        assert ready["workflow"]["steps"][1]["available"]
        assert (
            client.post(
                f"/api/projects/{pid}/lyrics", json={"text": "第一句\n第二句"}
            ).status_code
            == 200
        )


def test_dash_download_merges_and_validates_before_promoting(tmp_path, monkeypatch):
    import ktv_app.bilibili as module

    def data(client, endpoint, **params):
        if endpoint.endswith("view"):
            return {"pages": [{"cid": 123}]}
        return {
            "dash": {
                "video": [
                    {
                        "id": 64,
                        "codecs": "avc1",
                        "baseUrl": "https://cdn.bilivideo.com/video",
                    }
                ],
                "audio": [
                    {"codecs": "mp4a", "baseUrl": "https://cdn.bilivideo.com/audio"}
                ],
            }
        }

    def stream(client, urls, target, limit, progress):
        target.write_bytes(b"stream")
        return 6

    monkeypatch.setattr(module, "api_data", data)
    monkeypatch.setattr(module, "download_stream", stream)
    monkeypatch.setattr(module, "run", lambda cmd: Path(cmd[-1]).write_bytes(b"merged"))
    checked = []
    monkeypatch.setattr(module, "probe", lambda path, settings: checked.append(path))
    output = download_video(
        {"bvid": "BV18i421e7cJ", "page": 1}, tmp_path, Settings(), lambda message: None
    )
    assert output.read_bytes() == b"merged"
    assert checked == [tmp_path / "download.mp4"]
    assert not (tmp_path / "download.mp4").exists()
