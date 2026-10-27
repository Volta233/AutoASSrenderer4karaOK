import pytest

from ktv_app.aligners.anchor_policy import (
    POLICY,
    endpoints,
    build_parts,
    clean_tifa,
)
from ktv_app.completion import complete_timeline, remove_estimates
from ktv_app.subtitles import display_windows
from ktv_app.timeline import Line, Unit, Timeline, Style, quality


def unit(text, lo, a=None, b=None, id=None):
    return Unit(
        id=id or f"u{lo}",
        text=text,
        source_start=lo,
        source_end=lo + len(text),
        start=a,
        end=b,
    )


def timeline(lines, duration=60):
    return quality(
        Timeline(
            duration=duration, lines=lines, diagnostics={"boundary_policy": POLICY}
        )
    )


def test_missing_first_word_merges_previous_instead_of_using_middle_start():
    a = Line(id="a", text="前句", units=[unit("前句", 0, 10, 12, id="a-u")])
    b = Line(
        id="b",
        text="甲乙丙",
        units=[unit("甲", 0, id="b0"), unit("乙丙", 1, 15, 17, id="b1")],
    )
    t = timeline([a, b])
    assert endpoints(t.lines[1]) == (None, 17)
    parts, reasons = build_parts(t)
    assert (
        parts[0]["indices"] == [0, 1]
        and parts[0]["start"] == 10
        and parts[0]["end"] == 17
    )
    assert reasons[0]["reason"] == "missing_first_word"


def test_missing_last_word_merges_next_and_keeps_original_line_ids():
    a = Line(
        id="a",
        text="甲乙",
        units=[unit("甲", 0, 10, 11, id="a0"), unit("乙", 1, id="a1")],
    )
    b = Line(id="b", text="后句", units=[unit("后句", 0, 15, 17, id="b0")])
    parts, _ = build_parts(timeline([a, b]))
    assert parts[0]["line_ids"] == ["a", "b"] and parts[0]["end"] == 17


def test_entire_missing_sentence_transitively_merges_both_neighbours():
    lines = [
        Line(id="a", text="前", units=[unit("前", 0, 10, 11, id="a0")]),
        Line(id="b", text="缺句"),
        Line(id="c", text="后", units=[unit("后", 0, 20, 21, id="c0")]),
    ]
    t = clean_tifa(timeline(lines))
    parts, _ = build_parts(t)
    assert parts[0]["indices"] == [0, 1, 2]


def test_one_missing_word_uses_exact_neighbour_gap_without_rebuilding_native_words():
    line = Line(
        id="l",
        text="甲乙丙",
        units=[unit("甲", 0, 10, 11), unit("乙", 1), unit("丙", 2, 13, 14)],
    )
    out = complete_timeline(timeline([line]), rolling=True)
    assert [(u.start, u.end, u.timing_source) for u in out.lines[0].units] == [
        (10, 11, "model"),
        (11, 13, "estimated"),
        (13, 14, "model"),
    ]
    assert remove_estimates(out).lines[0].units[0].start == 10


def test_continuous_missing_words_use_character_weights_between_fixed_anchors():
    l = Line(
        id="l",
        text="甲乙乙丙丁丁丁戊",
        units=[
            unit("甲", 0, 10, 11),
            unit("乙乙", 1),
            unit("丙", 3),
            unit("丁丁丁", 4),
            unit("戊", 7, 14, 15),
        ],
    )
    out = complete_timeline(timeline([l]), rolling=True)
    assert [(u.start, u.end) for u in out.lines[0].units] == [
        (10, 11),
        (11, 12),
        (12, 12.5),
        (12.5, 14),
        (14, 15),
    ]


def test_tight_single_missing_word_does_not_erase_entire_tifa_line():
    l = Line(
        id="l",
        text="甲乙丙",
        units=[unit("甲", 0, 10, 11), unit("乙", 1), unit("丙", 2, 11.01, 12)],
    )
    out = complete_timeline(timeline([l]), rolling=True)
    assert out.lines[0].units[0].timing_source == "model"
    assert out.lines[0].units[-1].start == 11.01
    assert out.lines[0].units[1].start == 11 and out.lines[0].units[1].end == 11.01


def test_captured_collapsed_lala_is_quarantined_before_cutting_or_completion():
    a = Line(id="a", text="前句", units=[unit("前句", 0, 280, 282, id="a0")])
    b = Line(
        id="b",
        text="啦儿啦",
        units=[
            unit("啦", 0, 284.48, 284.50),
            unit("儿", 1, 284.50, 284.52),
            unit("啦", 2, 284.52, 284.53),
        ],
    )
    c = Line(id="c", text="后句", units=[unit("后句", 0, 288, 290, id="c0")])
    clean = clean_tifa(timeline([a, b, c], 300))
    assert all(u.start is None for u in clean.lines[1].units)
    assert clean.diagnostics["quarantined_native"][0]["reason"] == "collapsed_sentence"
    out = complete_timeline(clean, rolling=True)
    assert out.lines[1].start == 282 and out.lines[1].end == 288
    assert out.lines[0].units[0].start == 280 and out.lines[2].units[0].start == 288


def test_long_instrumental_hides_completed_line_and_delays_upcoming_line():
    lines = [
        Line(id=str(i), text="歌词", units=[unit("歌词", 0, a, a + 2, id=f"{i}-u")])
        for i, a in enumerate([10, 13, 40, 43])
    ]
    t = timeline(lines)
    w = display_windows(t, Style())
    assert w["1"]["end"] <= 15.5
    assert w["2"]["start"] == 38
    assert not [x for x in w.values() if x["start"] <= 30 < x["end"]]


def test_zero_space_missing_sentence_is_not_forced_into_millisecond_sweeps():
    lines = [
        Line(id="a", text="前句", units=[unit("前句", 0, 10, 12, id="a0")]),
        Line(id="b", text="需要补齐"),
        Line(id="c", text="后句", units=[unit("后句", 0, 12, 15, id="c0")]),
    ]
    out = complete_timeline(timeline(lines), rolling=True)
    assert out.lines[1].end - out.lines[1].start >= 1
    assert out.lines[0].units[0].end == 12
    assert out.diagnostics["auto_completion"]["released_conflicting_anchors"]
