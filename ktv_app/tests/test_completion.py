import json

from ktv_app.completion import complete_timeline, remove_estimates
from ktv_app.lyrics import preprocess
from ktv_app.timeline import Timeline, Line, Unit, Style, quality
from ktv_app.subtitles import write_bundle


def test_complete_zero_match_exports_every_line_and_labels_estimates(tmp_path):
    original = quality(
        Timeline(duration=20, lines=preprocess("甲乙丙\nHello world\n末句", "auto"))
    )
    complete = complete_timeline(original)
    assert all(l.start is not None and l.units for l in complete.lines)
    assert all(u.timing_source == "estimated" for l in complete.lines for u in l.units)
    assert all(l.start is None for l in original.lines)
    write_bundle(complete, Style(mode="single"), tmp_path)
    report = json.loads((tmp_path / "export_report.json").read_text(encoding="utf-8"))
    assert report["rendered_lines"] == report["total_lines"] == 3
    assert report["estimated_lines"] == 3
    assert (tmp_path / "lyrics.ass").read_text(encoding="utf-8-sig").count(
        "Dialogue:"
    ) == 3
    assert len((tmp_path / "lyrics.lrc").read_text(encoding="utf-8").splitlines()) == 3


def test_preserves_anchors_and_uses_review_cards_for_unbounded_edges():
    lines = preprocess("Head\nModel\nMiddle\nManual\nTail", "en")
    for index, a, b, source in [(1, 3, 4, "model"), (3, 8, 9, "manual")]:
        line = lines[index]
        line.units = [
            Unit(
                id=line.id + "-word",
                text=line.text,
                source_start=0,
                source_end=len(line.text),
                start=a,
                end=b,
                timing_source=source,
            )
        ]
    complete = complete_timeline(quality(Timeline(duration=12, lines=lines)))
    assert (complete.lines[1].start, complete.lines[1].end) == (3, 4)
    assert (complete.lines[3].start, complete.lines[3].end) == (8, 9)
    assert complete.lines[0].display_kind == "review" and complete.lines[0].start >= 9
    assert complete.lines[2].start >= 4 and complete.lines[2].end <= 8
    assert complete.lines[4].start >= 9 and complete.lines[4].end <= 12
    restored = remove_estimates(complete)
    assert [i for i, l in enumerate(restored.lines) if l.start is not None] == [1, 3]


def test_zero_space_preserves_anchor_and_outputs_review_cards(tmp_path):
    lines = preprocess("Before\nAnchor\nAfter", "en")
    line = lines[1]
    line.units = [
        Unit(
            id="native",
            text=line.text,
            source_start=0,
            source_end=len(line.text),
            start=0,
            end=2,
        )
    ]
    complete = complete_timeline(quality(Timeline(duration=3, lines=lines)))
    assert all(l.start is not None for l in complete.lines)
    assert complete.lines[1].units[0].start == 0 and complete.lines[1].units[0].end == 2
    assert complete.lines[0].display_kind == "review"
    write_bundle(complete, Style(mode="single"), tmp_path)
    assert (
        json.loads((tmp_path / "export_report.json").read_text(encoding="utf-8"))[
            "rendered_lines"
        ]
        == 3
    )


def test_fills_only_missing_words_and_repeated_completion_is_stable():
    line = Line(
        id="l",
        text="ONE TWO THREE",
        units=[
            Unit(id="one", text="ONE", source_start=0, source_end=3, start=2, end=3),
            Unit(id="two", text="TWO", source_start=4, source_end=7),
            Unit(
                id="three", text="THREE", source_start=8, source_end=13, start=5, end=6
            ),
        ],
    )
    original = quality(Timeline(duration=8, lines=[line]))
    complete = complete_timeline(original)
    one, two, three = complete.lines[0].units
    assert one.timing_source == three.timing_source == "model"
    assert two.timing_source == "estimated" and 3 <= two.start < two.end <= 5
    second = complete_timeline(complete)
    assert [(u.start, u.end, u.timing_source) for u in second.lines[0].units] == [
        (u.start, u.end, u.timing_source) for u in complete.lines[0].units
    ]
    restored = remove_estimates(second)
    assert restored.lines[0].units[1].start is None


def test_many_short_missing_rows_are_not_skipped_by_ass_slots(tmp_path):
    lines = preprocess("\n".join("缺失行" + str(i) for i in range(90)), "zh")
    timeline = complete_timeline(quality(Timeline(duration=98, lines=lines)))
    for mode in ("single", "double"):
        folder = tmp_path / mode
        write_bundle(timeline, Style(mode=mode), folder)
        report = json.loads((folder / "export_report.json").read_text(encoding="utf-8"))
        assert report["rendered_lines"] == 90 and report["unmatched_lines"] == 0
        saved = Timeline.model_validate_json(
            (folder / "timeline.json").read_text(encoding="utf-8")
        )
        assert all(l.start < l.end <= 98 for l in saved.lines)


def test_conflicting_native_units_keep_original_evidence():
    line = Line(
        id="l",
        text="AB",
        units=[
            Unit(id="a", text="A", source_start=0, source_end=1, start=2, end=5),
            Unit(id="b", text="B", source_start=1, source_end=2, start=3, end=4),
        ],
    )
    complete = complete_timeline(quality(Timeline(duration=8, lines=[line])))
    assert complete.lines[0].units[0].end == 5 and complete.lines[0].units[1].start == 3
    restored = remove_estimates(complete)
    assert (restored.lines[0].units[0].start, restored.lines[0].units[0].end) == (2, 5)
    assert "estimated_time" not in restored.lines[0].units[0].flags


def test_manual_correction_changes_estimated_provenance(tmp_path):
    from fastapi.testclient import TestClient
    from ktv_app.backend.app import create_app
    from ktv_app.config import Settings

    video = tmp_path / "video.mp4"
    video.write_bytes(b"CRUD test input")
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        p = client.post(
            "/api/projects", json={"video": str(video), "song": "test"}
        ).json()
        pid = p["id"]
        timeline = complete_timeline(
            Timeline(duration=10, lines=preprocess("甲乙", "zh"))
        )
        first = client.put(
            f"/api/projects/{pid}/timeline",
            json={"revision": 0, "timeline": timeline.model_dump()},
        ).json()
        data = first["timeline"]
        data["lines"][0]["units"][0]["start"] = 0.1
        second = client.put(
            f"/api/projects/{pid}/timeline",
            json={"revision": first["revision"], "timeline": data},
        )
        assert second.status_code == 200
        unit = second.json()["timeline"]["lines"][0]["units"][0]
        assert (
            unit["timing_source"] == "manual" and "estimated_time" not in unit["flags"]
        )
    app.state.projects.pool.shutdown(wait=True)
