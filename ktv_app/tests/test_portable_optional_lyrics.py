"""Portable interaction contracts, isolated from model inference and real API secrets."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ktv_app.backend.app import create_app
from ktv_app.backend.service import Projects, write_json
from ktv_app.config import Settings
from ktv_app.lyrics import search
from ktv_app.timeline import Timeline, Unit


def prepared(tmp_path):
    store = Projects(Settings(projects=tmp_path / "projects", device="cuda"))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"CRUD fixture only")
    p = store.create(video, "Song", "Artist", "en")
    folder = store.folder(p["id"])
    write_json(
        folder / "media.json", {"duration": 30, "audio_offset": 0, "audio_duration": 30}
    )
    return store, p["id"]


def test_no_import_uses_actual_web_source_without_ai(monkeypatch):
    calls = []
    record = {
        "id": 123,
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 30,
        "plainLyrics": "Actual lyrics",
    }

    def get(self, url, **kwargs):
        calls.append((url, kwargs))
        return httpx.Response(200, request=httpx.Request("GET", url), json=[record])

    monkeypatch.setattr(httpx.Client, "get", get)
    result = search(
        "Song",
        "Artist",
        30,
        Settings(),
    )
    assert result["candidates"][0]["text"] == "Actual lyrics"
    assert result["candidates"][0]["source"] == "https://lrclib.net/api/get/123"
    assert len(calls) == 1 and calls[0][1]["params"]["track_name"] == "Song"


def test_search_cleans_lrc_before_candidates_and_ignores_source_clock(monkeypatch):
    record = {
        "id": 123,
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 30,
        "syncedLyrics": "[ar:Artist]\n[99:00.00]First\n[00:01.00]Second",
    }
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kw: httpx.Response(
            200, request=httpx.Request("GET", url), json=[record]
        ),
    )
    result = search("Song", "Artist", 30, Settings())
    candidate = result["candidates"][0]
    assert candidate["text"] == "First\nSecond"
    assert candidate["recommendation"] == "recommended"
    assert candidate["warnings"] == []
    assert "synced" not in candidate and "lrc_last_time" not in candidate


def test_saved_lyrics_and_old_candidates_do_not_propagate_source_times(tmp_path):
    store, pid = prepared(tmp_path)
    try:
        folder = store.folder(pid)
        write_json(
            folder / "candidates.json",
            {
                "candidates": [
                    {
                        "id": "old",
                        "song": "Song",
                        "artist": "Artist",
                        "duration": 30,
                        "text": "[00:20.00]First\n[00:01.00]Second",
                        "synced": "[00:20.00]First\n[00:01.00]Second",
                        "lrc_last_time": 20,
                    }
                ]
            },
        )
        candidate = store.get(pid)["candidates"]["candidates"][0]
        assert candidate["text"] == "First\nSecond"
        assert "synced" not in candidate and "lrc_last_time" not in candidate
        for payload in (
            {"candidate": "old"},
            {"text": "[00:20.00]First\n[00:01.00]Second"},
        ):
            result = store.set_lyrics(pid, **payload)
            assert [line["text"] for line in result["lyrics"]] == ["First", "Second"]
            assert all(
                "hint" not in line and line["start"] is None
                for line in result["lyrics"]
            )
            for name in ("lyrics.txt", "lyrics.original.txt"):
                assert (folder / name).read_text(encoding="utf-8") == "First\nSecond"
        legacy = dict(result["lyrics"][0], hint=20)
        write_json(folder / "lines.json", [legacy])
        assert "hint" not in store.get(pid)["lyrics"][0]
    finally:
        store.pool.shutdown(wait=True)


def test_unavailable_lookup_returns_evidence_not_manufactured_lyrics(monkeypatch):
    monkeypatch.setattr(
        httpx.Client,
        "get",
        lambda self, url, **kwargs: httpx.Response(
            200, request=httpx.Request("GET", url), json=[]
        ),
    )
    monkeypatch.setattr(
        "ktv_app.lyrics.search_web",
        lambda query: [
            {
                "href": "https://example.com/song",
                "title": "Song",
                "body": "Lyrics version information",
            }
        ],
    )
    result = search("Song", "Artist", 30, Settings())
    assert result["candidates"] == [] and result["auto_id"] is None
    assert result["verification"]["web"]["sources"][0]["read_status"] == "snippet_only"


def test_import_supports_common_encodings_and_does_not_confirm_until_save(tmp_path):
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        for encoding in ("utf-8-sig", "utf-16", "gb18030"):
            response = client.post(
                "/api/lyrics/import",
                files={
                    "file": (
                        "song.lrc",
                        "[00:01.00]月光明亮".encode(encoding),
                        "text/plain",
                    )
                },
            )
            assert response.status_code == 200
            assert (
                response.json()["text"] == "月光明亮" and not response.json()["saved"]
            )
        assert (
            client.post(
                "/api/lyrics/import", files={"file": ("song.exe", b"invalid")}
            ).status_code
            == 400
        )
        assert (
            client.post(
                "/api/lyrics/import",
                files={"file": ("song.txt", b"x" * (2 * 1024 * 1024 + 1))},
            ).status_code
            == 400
        )
        assert client.get("/api/projects").json() == []


def test_optional_key_does_not_affect_local_health_or_lyric_step(tmp_path, monkeypatch):
    store, pid = prepared(tmp_path)
    calls = []
    monkeypatch.setattr(store.pool, "submit", lambda fn, *args: calls.append(args))
    try:
        store.set_lyrics(pid, text="Actual lyric line")
        assert store.get(pid)["workflow"]["steps"][2]["available"]
        for device in ("cuda",):
            store.submit(pid, "align", {"device": device})
            assert calls[-1][2]["device"] == device
            store.update(pid, busy=False)
        with pytest.raises(ValueError, match="未知"):
            store.submit(pid, "verify")
        with pytest.raises(ValueError):
            store.submit(pid, "align", {"device": "mps"})
    finally:
        store.pool.shutdown(wait=True)
    app = create_app(Settings(projects=tmp_path / "health"))
    with TestClient(app) as client:
        response = client.get("/api/health").json()
        assert "llm" not in response["checks"]
        assert response["default_device"] == "cuda" and "llm_configured" not in response


def test_device_selection_reaches_separation_and_alignment(tmp_path, monkeypatch):
    store, pid = prepared(tmp_path)
    store.set_lyrics(pid, text="One line")
    devices = []

    def separate(video, audio, media, folder, method, settings, event):
        devices.append(("kim", settings.device))
        return audio, {"method": "kim", "model_info": {"device": settings.device}}

    def align(audio, lines, duration, origin, work, settings, event, **kwargs):
        devices.append(("asr_tifa", settings.device))
        line = lines[0]
        line.units = [
            Unit(
                id="native",
                text=line.text,
                source_start=0,
                source_end=len(line.text),
                start=5,
                end=8,
            )
        ]
        return Timeline(duration=duration, origin=origin, lines=lines)

    monkeypatch.setattr(
        "ktv_app.backend.tasks.alignment.prepare_alignment_audio", separate
    )
    monkeypatch.setattr("ktv_app.backend.tasks.alignment.align_reliable_blocks", align)
    try:
        store._execute(pid, "align", {"device": "cuda"})
        assert devices == [("kim", "cuda"), ("asr_tifa", "cuda")]
        assert store.settings.device == "cuda"
        result = store.get(pid)
        assert (
            result["timeline"]["diagnostics"]["alignment_options"]["device"] == "cuda"
        )
        assert result["workflow"]["steps"][3]["available"]
    finally:
        store.pool.shutdown(wait=True)


def test_ui_has_no_llm_settings_or_preview_generation():
    frontend = Path(__file__).resolve().parents[1] / "frontend"
    html = (frontend / "index.html").read_text(encoding="utf-8")
    app_js = (frontend / "app.js").read_text(encoding="utf-8")
    assert "llm" not in html.lower()
    assert 'id="preview"' not in html
    assert "useAI" not in app_js
    assert not (frontend / "llm.js").exists()
