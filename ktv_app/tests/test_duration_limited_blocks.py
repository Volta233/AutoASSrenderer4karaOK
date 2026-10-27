import pytest

from ktv_app.aligners.block_planning import frozen_boundaries
from ktv_app.aligners.reliable_blocks import (
    build_duration_limited_plan,
    match_boundary_lines,
    partial_unique_cut,
)
from ktv_app.timeline import Line
from ktv_app.workflow import AlignmentOptions


def sparse_song():
    lines = [
        Line(
            id=str(i),
            text="白日依山尽黄河入海流欲穷"
            + chr(0x4E30 + i)
            + chr(0x5400 + i)
            + chr(0x5600 + i)
            + chr(0x5700 + i),
        )
        for i in range(8)
    ]
    tokens = [
        dict(text=c, start=10 + i * 20 + j * 0.5, end=10 + i * 20 + j * 0.5 + 0.4)
        for i, line in enumerate(lines)
        for j, c in enumerate(line.text[-4:])
    ]
    return lines, tokens


def test_sparse_unique_phrases_need_fourth_relaxation_and_every_input_is_bounded():
    lines, tokens = sparse_song()
    plan, _, _ = build_duration_limited_plan(lines, tokens, tokens, 0, 180)
    adaptive = plan["adaptive_relaxation"]
    assert adaptive["relaxations_used"] == 4 and adaptive["target_met"]
    assert all(not a["target_met"] for a in adaptive["attempts"][:-1])
    assert all(
        j["audio_end"] - j["audio_start"] <= 90
        for j in adaptive["attempts"][-1]["inputs"]
    )
    assert [i for b in plan["blocks"] for i in b["indices"]] == list(range(len(lines)))


@pytest.mark.parametrize(
    "failure", ["missing_pass", "conflicting_time", "missing_suffix", "repeated_phrase"]
)
def test_weak_cuts_still_need_two_pass_location_and_unique_phrase(failure):
    lines, tokens = sparse_song()
    second = tokens
    if failure == "missing_pass":
        second = []
    elif failure == "conflicting_time":
        second = [dict(t, start=t["start"] + 3, end=t["end"] + 3) for t in tokens]
    elif failure == "missing_suffix":
        tokens = [dict(t, text="错") if j % 4 == 3 else t for j, t in enumerate(tokens)]
        second = tokens
    elif failure == "repeated_phrase":
        lines = [Line(id=str(i), text=lines[0].text) for i in range(8)]
        tokens = [
            dict(t, text=lines[0].text[-4:][j % 4]) for j, t in enumerate(tokens[:-4])
        ]
        second = tokens
    plan, _, _ = build_duration_limited_plan(lines, tokens, second, 0, 180)
    assert not plan["adaptive_relaxation"]["target_met"]
    assert not plan["boundaries"]


def test_repeated_partial_phrases_require_same_count_and_order_and_need_level_five():
    lines, tokens = sparse_song()
    lines = [Line(id=str(i), text=lines[0].text) for i in range(8)]
    tokens = [dict(t, text=lines[0].text[-4:][j % 4]) for j, t in enumerate(tokens)]
    plan, _, _ = build_duration_limited_plan(lines, tokens, tokens, 0, 180)
    assert plan["adaptive_relaxation"]["target_met"]
    assert plan["adaptive_relaxation"]["relaxations_used"] == 5
    for cut in plan["boundaries"]:
        proof = cut["context_proof"]["phrase_evidence"]
        assert proof["occurrences_in_source_and_passes"] == [8, 8, 8]


def test_duration_goal_cannot_be_replaced_by_enough_blocks():
    lines, tokens = sparse_song()
    plan, _, _ = build_duration_limited_plan(lines, tokens, tokens, 0, 270)
    assert len(plan["blocks"]) >= 5
    assert not plan["adaptive_relaxation"]["target_met"]
    assert plan["adaptive_relaxation"]["attempts"][-1]["max_input_seconds"] > 90


def test_partial_anchor_cannot_use_characters_inside_a_whole_asr_token():
    lines = [
        Line(id="a", text="白日依山尽黄河入海流"),
        Line(id="b", text="明月照亮归路"),
    ]
    tokens = [dict(text="黄河入海流明月", start=10, end=13)]
    matches = {
        m["line_index"]: m
        for m in match_boundary_lines(lines, tokens, endpoint_chars=1)
    }
    keys = [l.text for l in lines]
    assert (
        partial_unique_cut(lines, 0, matches, matches, keys, ["黄河入海流明月"] * 2, 4)
        is None
    )


def test_formal_pipeline_always_requires_the_90_second_asr_route():
    assert AlignmentOptions().max_tifa_input_seconds == 90
    assert (
        AlignmentOptions(
            model="reliable_blocks_tifa", max_tifa_input_seconds=90
        ).max_tifa_input_seconds
        == 90
    )
    with pytest.raises(ValueError):
        AlignmentOptions(model="tifa", max_tifa_input_seconds=90)
    with pytest.raises(ValueError):
        AlignmentOptions(model="reliable_blocks_tifa", max_tifa_input_seconds=91)


def overrun_plan():
    return {
        "boundaries": [
            {"after_line_index": 0, "time": 35.6},
            {"after_line_index": 9, "time": 108.4},
            {"after_line_index": 11, "time": 119.4},
            {"after_line_index": 16, "time": 212.5},
            {"after_line_index": 18, "time": 232.5},
        ],
        "blocks": [
            {"indices": [0], "duration": 35.6},
            {"indices": list(range(1, 10)), "duration": 72.8},
            {"indices": [10, 11], "duration": 11.0},
            {"indices": [12, 13, 14, 15, 16], "duration": 93.1},
            {"indices": [17, 18], "duration": 20.0},
            {"indices": list(range(19, 22)), "duration": 34.1},
        ],
    }


def test_overrun_block_reopens_only_its_opening_cut():
    kept = frozen_boundaries(overrun_plan(), 90)
    assert [cut["after_line_index"] for cut in kept] == [0, 9, 16, 18]


def test_bounded_plan_freezes_every_cut():
    plan = overrun_plan()
    kept = frozen_boundaries(plan, 300)
    assert [cut["after_line_index"] for cut in kept] == [0, 9, 11, 16, 18]


def test_first_round_and_missing_plan_freeze_nothing():
    assert frozen_boundaries(None, 90) is None
    single = {
        "boundaries": [{"after_line_index": 3, "time": 200.0}],
        "blocks": [{"indices": list(range(0, 4)), "duration": 200.0}],
    }
    assert [cut["after_line_index"] for cut in frozen_boundaries(single, 90)] == [3]
