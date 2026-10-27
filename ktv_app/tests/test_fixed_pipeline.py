import json
from pathlib import Path
import re

import pytest

from ktv_app.backend.service import Projects, write_json
from ktv_app.config import Settings
from ktv_app.timeline import Timeline
from ktv_app.workflow import AlignmentOptions, alignment_identity


@pytest.mark.parametrize(
    "change",
    [
        {"model": "tifa"},
        {"vocal_separation": "none"},
        {"complete_missing": False},
        {"max_tifa_input_seconds": None},
        {"max_tifa_input_seconds": 60},
    ],
)
def test_formal_route_cannot_disable_any_alignment_stage(change):
    with pytest.raises(ValueError):
        AlignmentOptions(**change)


def prepared(tmp_path, **settings):
    tmp_path.mkdir(parents=True, exist_ok=True)
    store = Projects(Settings(projects=tmp_path / "projects", **settings))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"CRUD fixture only")
    p = store.create(video, "Song", "Artist", "en")
    write_json(
        store.folder(p["id"]) / "media.json",
        {"duration": 30, "audio_offset": 0, "sha256": "fixture"},
    )
    store.set_lyrics(p["id"], text="One lyric line")
    return store, p["id"]


def test_lyrics_need_no_ai_review_and_edits_invalidate_only_optional_review(
    tmp_path, monkeypatch
):
    store, pid = prepared(tmp_path)
    calls = []
    monkeypatch.setattr(store.pool, "submit", lambda fn, *args: calls.append(args))
    try:
        store.submit(pid, "align")
        assert calls[-1][1] == "align"
        store.update(pid, busy=False)
        p = store.get(pid)
        write_json(
            store.folder(pid) / "lyrics_review.json",
            {"status": "completed", "identity": alignment_identity(p)},
        )
        assert store.get(pid)["workflow"]["steps"][2]["available"]
        store.set_lyrics(pid, text="Changed lyric line")
        assert not store.get(pid).get("lyrics_review")
        assert store.get(pid)["workflow"]["steps"][2]["available"]
        store.submit(pid, "align")
        assert calls[-1][1] == "align"
    finally:
        store.pool.shutdown(wait=True)


def test_manual_timestamp_file_cannot_skip_the_model_pipeline(tmp_path):
    store, pid = prepared(tmp_path)
    try:
        p = store.get(pid)
        write_json(
            store.folder(pid) / "lyrics_review.json",
            {"status": "completed", "identity": alignment_identity(p)},
        )
        fake = Timeline(duration=30, diagnostics={"step3_completed": True})
        write_json(store.folder(pid) / "timeline.json", fake.model_dump())
        for action in ("complete", "export"):
            with pytest.raises(ValueError, match="完整对齐"):
                store.submit(pid, action)
    finally:
        store.pool.shutdown(wait=True)


def test_frontend_has_no_algorithm_switches_and_no_missing_control_references():
    root = Path(__file__).resolve().parents[1] / "frontend"
    html = (root / "index.html").read_text(encoding="utf-8")
    removed = {
        "alignmentModel",
        "vocalSeparation",
        "limitTifa",
        "completeMissing",
        "llmWebSearch",
        "autoComplete",
        "mode",
        "lead",
    }
    ids = set(re.findall(r'id="([^"]+)"', html))
    assert not ids & removed
    assert "lyricsFile" in ids
    assert not {"useAI", "llmEnabled", "llmModels", "preview"} & ids
    for file in root.glob("*.js"):
        refs = set(re.findall(r"\$\('([^']+)'\)", file.read_text(encoding="utf-8")))
        assert refs <= ids, (file.name, refs - ids)
