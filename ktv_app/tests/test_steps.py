import json
import pytest
import httpx
from fastapi.testclient import TestClient
from ktv_app.backend.app import create_app
from ktv_app.backend.service import write_json
from ktv_app.config import Settings
from ktv_app.lyrics import preprocess, search, candidate_evidence
from ktv_app.completion import complete_timeline
from ktv_app.subtitles import write_bundle
from ktv_app.timeline import Timeline, Unit, quality, Style
from ktv_app.workflow import timing_evidence, AlignmentOptions, alignment_identity


def anchored():
    lines = preprocess("First words\nMissing phrase\nLast words", "en")
    for index, start, end in [(0, 10, 12), (2, 17, 19)]:
        l = lines[index]
        l.units = [
            Unit(
                id=l.id + "-native",
                text=l.text,
                source_start=0,
                source_end=len(l.text),
                start=start,
                end=end,
            )
        ]
    return quality(Timeline(duration=30, lines=lines))


def test_ten_second_intro_and_outro_not_filled_with_fake_singing(tmp_path):
    original = anchored()
    complete = complete_timeline(original)
    assert min(l.start for l in complete.lines) == 10
    assert complete.lines[0].units[0].start == 10 and complete.lines[2].end == 19
    write_bundle(complete, Style(mode="single", tail=0), tmp_path)
    ass = (tmp_path / "lyrics.ass").read_text(encoding="utf-8-sig")
    assert "Dialogue: 0,0:00:10.00" in ass
    assert r"\kf500}Missing" not in ass
    assert "Missing" in ass
    assert original.lines[1].start is None


def test_no_anchor_means_static_review_cards_not_a_guessed_opening(tmp_path):
    complete = complete_timeline(
        Timeline(duration=30, lines=preprocess("First\nSecond", "en"))
    )
    assert all(l.display_kind == "review" and l.start >= 24 for l in complete.lines)
    write_bundle(complete, Style(), tmp_path)
    ass = (tmp_path / "lyrics.ass").read_text(encoding="utf-8-sig")
    assert ass.count("Dialogue:") == 2 and "未定位" in ass and r"\kf" not in ass


def test_user_bounds_fill_all_lines_but_never_rewrite_native_anchors():
    t = complete_timeline(
        Timeline(duration=30, lines=preprocess("First\nSecond", "en")), 10, 20
    )
    assert t.lines[0].start == 10 and t.lines[-1].end == 20
    assert all(l.display_kind == "karaoke" for l in t.lines)
    conflicting = anchored()
    complete = complete_timeline(conflicting, 14, 25)
    assert (
        complete.lines[0].start == 10
    )  # A bad manual bound cannot corrupt acoustic evidence.


def test_export_is_a_frozen_timestamp_snapshot_with_or_without_estimate_display(
    tmp_path,
):
    t = complete_timeline(anchored())
    t.revision = 7
    for enabled in (True, False):
        folder = tmp_path / str(enabled)
        write_bundle(t, Style(auto_complete=enabled), folder)
        assert (
            json.loads((folder / "timeline.json").read_text(encoding="utf-8"))
            == t.model_dump()
        )
        assert (
            json.loads((folder / "export_report.json").read_text(encoding="utf-8"))[
                "timeline_revision"
            ]
            == 7
        )
    untimed = quality(Timeline(duration=10, lines=preprocess("Not aligned", "en")))
    write_bundle(untimed, Style(), tmp_path / "raw")
    assert (
        json.loads((tmp_path / "raw" / "timeline.json").read_text(encoding="utf-8"))[
            "lines"
        ][0]["start"]
        is None
    )


def test_coverage_does_not_inflate_after_completion_or_repeated_character_spans():
    raw = anchored()
    before, after = (
        timing_evidence(raw.model_dump()),
        timing_evidence(complete_timeline(raw).model_dump()),
    )
    assert before["model_coverage"] == after["model_coverage"] < 1
    assert after["estimated_coverage"] > 0 and after["missing_coverage"] == 0
    assert after["conflicting_units"] == 0


def test_wrong_recording_duration_cannot_get_a_high_identity_score():
    r = {
        "trackName": "Song",
        "artistName": "Artist",
        "duration": 230,
        "syncedLyrics": "[03:45.00]End",
    }
    e = candidate_evidence("Song", "Artist", 98, r)
    assert e["score"] <= 0.65 and e["requires_version_review"]
    assert (
        e["evidence"]["title_similarity"] == 1
        and e["evidence"]["duration_difference_seconds"] == 132
    )
    missing = candidate_evidence(
        "Song", "", 98, dict(r, duration=None, syncedLyrics=None)
    )
    assert (
        missing["evidence"]["artist_similarity"] is None
        and missing["evidence"]["duration_difference_seconds"] is None
    )


def test_step_contracts_do_not_cascade_and_invalidate_timestamps_after_lyric_changes(
    tmp_path, monkeypatch
):
    settings = Settings(projects=tmp_path / "flows")
    app = create_app(settings)
    calls = []
    monkeypatch.setattr(
        app.state.projects.pool, "submit", lambda fn, *args: calls.append(args)
    )
    video = tmp_path / "input.mp4"
    video.write_bytes(b"CRUD only")
    with TestClient(app) as c:
        p = c.post("/api/projects", json={"video": str(video), "song": "test"}).json()
        pid = p["id"]
        assert not p["busy"] and not calls
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/1", json={"action": "align"}
            ).status_code
            == 400
        )
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/2", json={"action": "search"}
            ).status_code
            == 400
        )
        response = c.post(f"/api/workflows/{pid}/steps/1", json={"action": "media"})
        assert response.status_code == 200 and calls[-1][1] == "media"
        app.state.projects.update(pid, busy=False)
        write_json(
            settings.projects / pid / "media.json", {"duration": 30, "audio_offset": 0}
        )
        c.post(f"/api/projects/{pid}/lyrics", json={"text": "Saved words"})
        assert c.get(f"/api/projects/{pid}").json()["workflow"]["steps"][2]["available"]
        current = app.state.projects.get(pid)
        write_json(
            settings.projects / pid / "lyrics_review.json",
            {"status": "completed", "identity": alignment_identity(current)},
        )
        assert c.get(f"/api/projects/{pid}").json()["workflow"]["steps"][2]["available"]
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/4", json={"action": "export"}
            ).status_code
            == 400
        )
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/3",
                json={"action": "align", "options": {"model": "invalid"}},
            ).status_code
            == 400
        )
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/3",
                json={"action": "align", "options": {"vocal_start": 31}},
            ).status_code
            == 400
        )
        t = complete_timeline(
            Timeline(duration=30, lines=preprocess("Saved words", "en")), 10, 20
        )
        t.diagnostics["step3_completed"] = True
        app.state.projects.update(
            pid,
            alignment_completed={
                "identity": alignment_identity(app.state.projects.get(pid))
            },
        )
        saved = c.put(
            f"/api/projects/{pid}/timeline",
            json={"revision": 0, "timeline": t.model_dump()},
        ).json()
        assert saved["workflow"]["steps"][3]["available"]
        changed = c.post(
            f"/api/projects/{pid}/lyrics", json={"text": "New lyrics"}
        ).json()
        assert (
            not changed["workflow"]["steps"][2]["complete"]
            and not changed["workflow"]["steps"][3]["available"]
        )
        assert (
            c.post(
                f"/api/workflows/{pid}/steps/4", json={"action": "export"}
            ).status_code
            == 400
        )
    app.state.projects.pool.shutdown(wait=True)


def test_bad_or_nonfinite_ranges_are_rejected():
    for values in (
        {"vocal_start": float("nan")},
        {"vocal_start": float("inf")},
        {"vocal_start": 10, "vocal_end": 2},
    ):
        with pytest.raises(ValueError):
            AlignmentOptions.model_validate(values)
