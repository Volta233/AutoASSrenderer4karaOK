import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from ktv_app.backend.app import create_app
from ktv_app.config import Settings
from ktv_app.lyrics import preprocess
from ktv_app.timeline import Line, Unit, Timeline, Style, quality
from ktv_app.subtitles import write_bundle, clock
from ktv_app.aligners.tifa import parse_result


def timed():
    return Timeline(
        duration=12,
        lines=[
            Line(
                id="a",
                text="Hello music!",
                language="en",
                units=[
                    Unit(
                        id="a0",
                        text="Hello",
                        source_start=0,
                        source_end=5,
                        start=2,
                        end=3,
                    ),
                    Unit(
                        id="a1",
                        text="music",
                        source_start=6,
                        source_end=11,
                        start=5,
                        end=6,
                    ),
                ],
            )
        ],
    )


def test_ass_sweep_lead_pause_and_lrc(tmp_path):
    t = quality(timed())
    write_bundle(t, Style(mode="single", lead=1), tmp_path)
    text = (tmp_path / "lyrics.ass").read_text(encoding="utf-8-sig")
    assert r"{\k100}" in text  # Advance at 2s despite line appearing at 1s.
    assert r"{\kf100}Hello" in text
    assert r"{\k200}" in text  # Singing resumes at 5s after a 2s pause.
    assert r"{\kf100} music" in text
    assert "<00:05.00> music<00:06.00>" in (tmp_path / "lyrics.lrc").read_text(
        encoding="utf-8"
    )


def test_normalization_is_traceable_and_source_times_are_discarded():
    lines = preprocess(
        "[00:20.00][00:40.00]猫が Ｈｅｌｌｏ！\n[ar:Artist]\n作曲：某人", "ja"
    )
    assert len(lines) == 1 and lines[0].text == "猫が Ｈｅｌｌｏ！"
    assert lines[0].normalized == "猫が Hello!" and lines[0].start is None
    assert "hint" not in lines[0].model_dump()
    assert preprocess("心里[爱你]", "zh")[0].text == "心里[爱你]"


def test_no_fake_timestamps(tmp_path):
    t = quality(timed())
    t.lines[0].units[0].start = t.lines[0].units[0].end = None
    t = quality(t)
    write_bundle(t, Style(auto_complete=False), tmp_path)
    saved = json.loads((tmp_path / "timeline.json").read_text(encoding="utf-8"))
    assert saved["lines"][0]["units"][0]["start"] is None
    assert (
        json.loads((tmp_path / "export_report.json").read_text(encoding="utf-8"))[
            "untimed_units"
        ]
        == 1
    )
    assert "<00:05.00> music" in (tmp_path / "lyrics.lrc").read_text(encoding="utf-8")
    ass = (tmp_path / "lyrics.ass").read_text(encoding="utf-8-sig")
    assert "Hello" in ass and r"{\kf100}Hello" not in ass


def test_nan_and_out_of_bounds_rejected():
    data = timed().model_dump()
    for value in (float("nan"), -1, 15):
        data["lines"][0]["units"][0]["start"] = value
        with pytest.raises(ValidationError):
            Timeline.model_validate(data)


def test_clock_rollover():
    assert clock(59.999) == "0:01:00.00"
    assert clock(3601.25, True) == "60:01.25"


def test_japanese_semantic_word_keeps_multiple_readings(tmp_path):
    import textgrid

    tg = textgrid.TextGrid(maxTime=3)
    for name, parts in [
        ("texts", [(1, 2, "猫")]),
        ("words", [(1, 1.4, "ne"), (1.4, 2, "ko")]),
        ("phones", [(1, 1.1, "n"), (1.1, 1.4, "e"), (1.4, 1.5, "k"), (1.5, 2, "o")]),
    ]:
        tier = textgrid.IntervalTier(name=name, maxTime=3)
        for a, b, text in parts:
            tier.add(a, b, text)
        tg.append(tier)
    tg.write(str(tmp_path / "test.TextGrid"))
    (tmp_path / "trace.json").write_text(
        json.dumps([{"line_id": "ja", "text": "猫"}]), encoding="utf-8"
    )
    t = parse_result(
        [Line(id="ja", text="猫！", language="ja")],
        tmp_path / "test.TextGrid",
        tmp_path / "trace.json",
        3,
        0,
    )
    u = t.lines[0].units[0]
    assert (
        u.text == "猫"
        and (u.start, u.end) == (1, 2)
        and len(u.readings) == 2
        and len(u.phones) == 4
    )


def test_edit_conflict_undo_and_security(tmp_path):
    video = tmp_path / "fake.mp4"
    video.write_bytes(b"placeholder for CRUD, not a media inference test")
    app = create_app(Settings(projects=tmp_path / "projects"))
    with TestClient(app) as client:
        p = client.post(
            "/api/projects", json={"video": str(video), "song": "test"}
        ).json()
        pid = p["id"]
        data = {"revision": 0, "timeline": quality(timed()).model_dump()}
        result = client.put(f"/api/projects/{pid}/timeline", json=data)
        assert result.status_code == 200
        assert client.put(f"/api/projects/{pid}/timeline", json=data).status_code == 409
        t = result.json()["timeline"]
        t["lines"][0]["units"][0]["start"] = 2.2
        assert (
            client.put(
                f"/api/projects/{pid}/timeline", json={"revision": 1, "timeline": t}
            ).status_code
            == 200
        )
        undo = client.post(f"/api/projects/{pid}/undo", json={}).json()
        assert (
            undo["timeline"]["lines"][0]["units"][0]["start"] == 2
            and undo["revision"] == 3
        )
        assert (
            client.post(
                "/api/projects",
                headers={"Origin": "https://evil.example"},
                json={"video": str(video)},
            ).status_code
            == 403
        )
        assert (
            client.get("/api/health", headers={"Host": "evil.example"}).status_code
            == 403
        )


def test_model_missing_is_explicit(tmp_path):
    from ktv_app.aligners.tifa import TIFAAdapter

    with pytest.raises(FileNotFoundError, match="权重"):
        TIFAAdapter(Settings(tifa_model=tmp_path / "missing.pt")).align(
            tmp_path / "none.wav", [], 4, 0, tmp_path / "align"
        )
