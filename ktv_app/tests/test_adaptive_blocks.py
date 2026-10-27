import pytest

from ktv_app.aligners.reliable_blocks import (
    build_adaptive_plan,
    build_plan,
    match_boundary_lines,
)
from ktv_app.timeline import Line


def song(mode="strict", count=12):
    lines = [
        Line(id=str(i), text=f"第{i}行青山{chr(0x5200 + i)}明月照亮远方归路")
        for i in range(count)
    ]
    tokens = []
    for i, line in enumerate(lines):
        chars = list(line.text)
        if mode != "strict":
            chars[1] = chars[-2] = "错"
        if mode == "second":
            chars[0] = chars[3] = "错"
        tokens.extend(
            dict(text=c, start=5 + i * 14 + j * 0.5, end=5 + i * 14 + j * 0.5 + 0.4)
            for j, c in enumerate(chars)
        )
    return lines, tokens


@pytest.mark.parametrize(
    "mode,expected_level", [("strict", 0), ("first", 1), ("second", 2)]
)
def test_only_relaxes_as_far_as_needed_and_preserves_every_lyric_row(
    mode, expected_level
):
    lines, tokens = song(mode)
    plan, _, _ = build_adaptive_plan(lines, tokens, tokens, 0, 180)
    adaptive = plan["adaptive_relaxation"]
    assert adaptive["minimum_blocks"] == 3 and adaptive["target_met"]
    assert adaptive["relaxations_used"] == expected_level
    assert len(adaptive["attempts"]) == expected_level + 1
    assert [i for block in plan["blocks"] for i in block["indices"]] == list(
        range(len(lines))
    )
    for cut in plan["boundaries"]:
        assert cut["verification_level"] == expected_level
        assert all(record["suffix_confirmed"] for record in cut["two_pass_evidence"])


@pytest.mark.parametrize(
    "seconds,minimum", [(60, 1), (60.01, 2), (120, 2), (120.01, 3)]
)
def test_trigger_uses_ceiling_of_audio_duration_and_ignores_origin(seconds, minimum):
    lines, tokens = song()
    plan, _, _ = build_adaptive_plan(lines, [], [], 10, 10 + seconds)
    assert plan["adaptive_relaxation"]["minimum_blocks"] == minimum
    assert plan["adaptive_relaxation"]["relaxations_used"] == (0 if minimum == 1 else 2)


@pytest.mark.parametrize("missing", ["one_pass", "both_passes", "large_time_conflict"])
def test_two_relaxations_never_invent_missing_or_conflicting_word_times(missing):
    lines, tokens = song()
    first, second = tokens, tokens
    if missing == "one_pass":
        second = []
    elif missing == "both_passes":
        first = second = []
    else:
        second = [dict(t, start=t["start"] + 3, end=t["end"] + 3) for t in tokens]
    plan, _, _ = build_adaptive_plan(lines, first, second, 0, 180)
    assert plan["adaptive_relaxation"]["relaxations_used"] == 2
    assert len(plan["adaptive_relaxation"]["attempts"]) == 3
    assert not plan["adaptive_relaxation"]["target_met"]
    assert len(plan["blocks"]) == 1 and not plan["boundaries"]


def test_cannot_cut_inside_a_whole_asr_word_even_at_second_relaxation():
    lines = [Line(id="a", text="春风吹过青山"), Line(id="b", text="明月照亮远方")]
    tokens = [dict(text="春风吹过青山明月照亮远方", start=15, end=18)]
    plan, _, _ = build_adaptive_plan(lines, tokens, tokens, 0, 120)
    assert (
        not plan["boundaries"] and plan["adaptive_relaxation"]["relaxations_used"] == 2
    )


def test_relaxation_keeps_already_chosen_strict_cut_time_and_lyric_ownership():
    lines, tokens = song("first")
    for i in [0, 1, 2, 3]:
        start = sum(len(line.text) for line in lines[:i])
        for j, c in enumerate(lines[i].text):
            tokens[start + j]["text"] = c
    matches = match_boundary_lines(lines, tokens)
    strict = build_plan(
        lines,
        matches,
        matches,
        0,
        600,
        target=25,
        fine_anchors=True,
        min_seconds=10,
        max_seconds=45,
        tail_seconds=8,
        observed_first="".join(t["text"] for t in tokens),
        observed_second="".join(t["text"] for t in tokens),
    )
    assert strict["boundaries"]
    plan, _, _ = build_adaptive_plan(lines, tokens, tokens, 0, 600)
    assert plan["adaptive_relaxation"]["relaxations_used"] > 0
    selected = {cut["after_line_index"]: cut for cut in plan["boundaries"]}
    for old in strict["boundaries"]:
        assert selected[old["after_line_index"]]["time"] == old["time"]
        assert selected[old["after_line_index"]]["verification_level"] == 0


def test_repeated_lyrics_with_wrong_occurrence_count_stay_unresolved():
    lines = [
        Line(id=str(i), text=text)
        for i, text in enumerate(["青山明月照亮路", "春风吹过远方人"] * 3)
    ]
    tokens = [
        dict(text=c, start=5 + i * 0.5, end=5 + i * 0.5 + 0.4)
        for i, c in enumerate("".join(line.text for line in lines[:2]))
    ]
    plan, _, _ = build_adaptive_plan(lines, tokens, tokens, 0, 180)
    assert not plan["boundaries"] and not plan["adaptive_relaxation"]["target_met"]


def test_alignment_route_runs_asr_once_and_records_relaxation_before_tifa(
    tmp_path, monkeypatch
):
    from types import SimpleNamespace
    import soundfile as sf
    import ktv_app.aligners.reliable_blocks as module
    from ktv_app.timeline import Timeline, Unit

    lines, tokens = song("second")
    calls = []

    def locate(*args, **kwargs):
        calls.append(kwargs["profile"])
        return dict(tokens=tokens, verification_tokens=tokens, chunks=[]), None

    def run_blocks(
        audio,
        source,
        blocks,
        first,
        second,
        duration,
        origin,
        limit,
        work,
        settings,
        event,
    ):
        results = []
        for block in blocks:
            selected = [source[i].model_copy(deep=True) for i in block["indices"]]
            span = (block["core_end"] - block["core_start"]) / len(selected)
            for i, line in enumerate(selected):
                start = block["core_start"] + (i + 0.1) * span
                line.units = [
                    Unit(
                        id=line.id + "-word",
                        text=line.text,
                        source_start=0,
                        source_end=len(line.text),
                        start=start,
                        end=start + span * 0.5,
                    )
                ]
            block.update(
                boundary_native_lines=[
                    selected[0].model_dump(),
                    selected[-1].model_dump(),
                ],
                native_boundary_violations=[],
                effective_audio_bounds=[block["core_start"], block["core_end"]],
            )
            results.append(
                (
                    block,
                    Timeline(
                        duration=duration,
                        lines=selected,
                        diagnostics=dict(
                            unit_sources={
                                u.id: "tifa_global" for l in selected for u in l.units
                            }
                        ),
                    ),
                )
            )
        return results

    monkeypatch.setattr(module, "locate", locate)
    monkeypatch.setattr(module, "japanese_reading_table", lambda *args: ({}, {}))
    monkeypatch.setattr(module, "run_blocks", run_blocks)
    monkeypatch.setattr(sf, "info", lambda *args: SimpleNamespace(duration=180))
    events = []
    result = module.align_reliable_blocks(
        tmp_path / "input.wav", lines, 180, 0, tmp_path, object(), events.append
    )
    adaptive = result.diagnostics["block_plan"]["adaptive_relaxation"]
    assert calls == ["asr_fine_blocks_v2"]
    assert (
        adaptive["relaxations_used"] == 2
        and adaptive["target_met"]
        and adaptive["final_target_met"]
    )
    assert [l.id for l in result.lines] == [l.id for l in lines]
    assert any("第 1/2 次条件放宽" in message for message in events)
    assert any("第 2/2 次条件放宽" in message for message in events)
