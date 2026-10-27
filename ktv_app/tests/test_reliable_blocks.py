import pytest

from ktv_app.aligners.reliable_blocks import (
    build_plan,
    context_job,
    seam_groups,
    strong_match,
    run_blocks,
    match_boundary_lines,
    repeat_proof,
    match_text,
    repair_seams,
    bounded_seam_groups,
    retry_seam_gaps,
)
from ktv_app.aligners.anchor_policy import POLICY
from ktv_app.completion import complete_timeline, remove_estimates
from ktv_app.timeline import Line, Unit, Timeline, quality
from ktv_app.workflow import AlignmentOptions


def source(count=24):
    return [Line(id=f"l{i}", text=f"这是第{i}段歌词") for i in range(count)]


def matches(lines, shift=0):
    return [
        dict(
            line_index=i,
            line_id=line.id,
            start=5 + 4 * i + shift,
            end=8 + 4 * i + shift,
            recognized_text=line.text,
            coverage=1,
            similarity=1,
        )
        for i, line in enumerate(lines)
    ]


def word(line, text, pos, a=None, b=None):
    return Unit(
        id=f"{line}-{pos}",
        text=text,
        source_start=pos,
        source_end=pos + len(text),
        start=a,
        end=b,
    )


def test_only_verified_complete_line_gaps_split_and_every_source_row_owned_once():
    lines = source()
    plan = build_plan(lines, matches(lines), matches(lines, 0.1), 0, 105)
    assert len(plan["blocks"]) == 2
    assert 40 <= plan["blocks"][0]["duration"] <= 55
    assert [i for b in plan["blocks"] for i in b["indices"]] == list(range(24))
    for cut in plan["boundaries"]:
        assert cut["pause_start"] < cut["time"] < cut["pause_end"]
        assert len(cut["context_indices"]) >= 4


def test_shifted_pass_disagreement_keeps_long_block_instead_of_inventing_cut():
    lines = source()
    plan = build_plan(lines, matches(lines), matches(lines, 2), 0, 105)
    assert not plan["boundaries"] and plan["blocks"][0]["over_target"]
    assert all(
        r["reason"] == "shifted_pass_timestamp_disagreement"
        for r in plan["rejected_boundaries"]
    )


def test_repeated_chorus_without_distinguishing_observed_context_rejected():
    lines = [
        Line(id=str(i), text=text)
        for i, text in enumerate(
            ["重复甲歌词", "重复乙歌词", "重复丙歌词", "重复丁歌词"] * 2
        )
    ]
    evidence = matches(lines)[:4]
    plan = build_plan(lines, evidence, evidence, 0, 120)
    assert not plan["boundaries"]
    assert any(
        r["reason"] == "repeated_or_unverified_context"
        for r in plan["rejected_boundaries"]
    )


def test_no_pause_or_one_pass_evidence_cannot_create_cut():
    lines = source()
    overlapping = matches(lines)
    for m in overlapping:
        m["end"] = m["start"] + 4.5
    assert not build_plan(lines, overlapping, overlapping, 0, 120)["boundaries"]
    assert not build_plan(lines, matches(lines), [], 0, 120)["boundaries"]


def test_incomplete_first_or_last_word_cannot_certify_boundary():
    line = Line(id="a", text="春风吹过山顶")
    m = dict(similarity=0.95, coverage=0.95, recognized_text="风吹过山顶")
    assert not strong_match(line, m)
    m["recognized_text"] = "春风吹过山"
    assert not strong_match(line, m)


def test_overlap_includes_full_context_and_actual_audio_origin():
    lines = source()
    evidence = {m["line_index"]: m for m in matches(lines, 10)}
    block = dict(indices=list(range(5, 12)), core_start=33.5, core_end=61.5)
    job = context_job(block, lines, evidence, evidence, 10, 115)
    assert job["context_indices"] == list(range(4, 13))
    assert job["audio_start"] == pytest.approx(evidence[4]["start"] - 0.4)
    assert job["audio_end"] == pytest.approx(evidence[12]["end"] + 0.4)


def test_completion_does_not_borrow_gap_time_from_next_audio_block():
    a = Line(
        id="a", text="甲乙", units=[word("a", "甲", 0, 44, 45), word("a", "乙", 1)]
    )
    b = Line(
        id="b", text="丙丁", units=[word("b", "丙", 0), word("b", "丁", 1, 60, 61)]
    )
    t = quality(
        Timeline(
            duration=100,
            lines=[a, b],
            diagnostics={
                "block_policy": "reliable_blocks_v1",
                "boundary_policy": POLICY,
                "alignment_blocks": [
                    dict(id="0", line_ids=["a"], effective_audio_bounds=[0, 50]),
                    dict(id="1", line_ids=["b"], effective_audio_bounds=[50, 100]),
                ],
            },
        )
    )
    out = complete_timeline(t, rolling=True)
    assert out.lines[0].units[-1].end < 50
    assert out.lines[1].units[0].start >= 50
    assert (out.lines[0].units[0].start, out.lines[1].units[-1].start) == (44, 60)
    assert (
        out.diagnostics["auto_completion"]["policy"]
        == "block_fixed_anchor_gap_interpolation"
    )
    restored = remove_estimates(out)
    assert (
        restored.lines[0].units[-1].start is None
        and restored.lines[1].units[0].start is None
    )


def test_seam_violation_merges_adjacent_blocks_for_retry():
    a = quality(
        Timeline(
            duration=100,
            lines=[Line(id="a", text="甲", units=[word("a", "甲", 0, 40, 49)])],
        )
    )
    b = quality(
        Timeline(
            duration=100,
            lines=[Line(id="b", text="乙", units=[word("b", "乙", 0, 52, 54)])],
        )
    )
    results = [
        (
            dict(
                core_end=50,
                native_boundary_violations=[dict(line_id="a", start=30, end=65)],
            ),
            a,
        ),
        (dict(core_end=100, native_boundary_violations=[]), b),
    ]
    assert seam_groups(results) == [[0, 1]]
    results[0][0]["native_boundary_violations"] = []
    assert seam_groups(results) == [[0], [1]]


def test_context_rows_are_discarded_by_id_and_native_outside_core_is_quarantined(
    tmp_path, monkeypatch
):
    import ktv_app.aligners.block_execution as module

    lines = [Line(id=str(i), text="歌词") for i in range(4)]
    records = {m["line_index"]: m for m in matches(lines)}
    block = dict(id="b", indices=[1, 2], line_ids=["1", "2"], core_start=9, core_end=16)
    monkeypatch.setattr(module, "crop", lambda *args: None)

    def fake_align(audio, selected, duration, origin, *args):
        for i, line in enumerate(selected):
            line.units = [
                word(line.id, line.text, 0, origin + i * 3, origin + i * 3 + 2)
            ]
        return quality(
            Timeline(
                duration=duration,
                origin=origin,
                lines=selected,
                diagnostics={
                    "unit_sources": {
                        u.id: "tifa_global" for l in selected for u in l.units
                    }
                },
            )
        )

    monkeypatch.setattr(module, "run_anchor_alignment", fake_align)
    results = run_blocks(
        tmp_path / "audio.wav",
        lines,
        [block],
        records,
        records,
        40,
        0,
        40,
        tmp_path,
        object(),
        lambda _: None,
    )
    result = results[0][1]
    assert [line.id for line in result.lines] == ["1", "2"]
    assert result.lines[0].units[0].start is None
    assert results[0][0]["native_boundary_violations"]


def test_new_option_is_valid():
    assert (
        AlignmentOptions(model="reliable_blocks_tifa").model == "reliable_blocks_tifa"
    )


def character_tokens(lines):
    return [
        dict(text=char, start=1 + i * 0.4, end=1 + i * 0.4 + 0.3)
        for i, char in enumerate("".join(line.text for line in lines))
    ]


def test_character_context_keeps_exact_asr_word_times_and_missing_edge_is_not_certified():
    lines = [Line(id="a", text="青山明月"), Line(id="b", text="春风吹来")]
    tokens = [
        dict(text="青山", start=10, end=12),
        dict(text="明月", start=13, end=15),
        dict(text="春风", start=16, end=17),
        dict(text="吹", start=18, end=19),
    ]
    records = match_boundary_lines(lines, tokens, origin=3)
    assert records[0]["prefix_confirmed"] and records[0]["suffix_confirmed"]
    assert records[0]["start"] == 13 and records[0]["end"] == 18
    assert records[1]["prefix_confirmed"] and not records[1]["suffix_confirmed"]
    assert records[1]["end"] == 22  # No made-up time for missing 来.


def test_repeated_boundary_requires_counts_and_occurrence_order_in_both_passes():
    lines = [
        Line(id=str(i), text=text)
        for i, text in enumerate(["青山明月", "春风吹来", "湖水清清", "白云悠悠"] * 2)
    ]
    tokens = character_tokens(lines)
    records = {m["line_index"]: m for m in match_boundary_lines(lines, tokens)}
    observed = "".join(match_text(t["text"]) for t in tokens)
    context, proof = repeat_proof(lines, 0, records, records, observed, observed)
    assert proof["expected_count"] == proof["first_count"] == proof["second_count"] == 2
    assert proof["occurrence"] == 1
    assert (
        repeat_proof(lines, 0, records, records, observed + "明月春风", observed)
        is None
    )


def test_confirmed_bracket_can_resolve_one_occurrence_without_using_untrusted_cuts():
    lines = [
        Line(id=str(i), text=text)
        for i, text in enumerate(["青山明月", "春风吹来", "湖水清清", "白云悠悠"] * 2)
    ]
    records = {
        m["line_index"]: m for m in match_boundary_lines(lines, character_tokens(lines))
    }
    observed = "".join(match_text(line.text) for line in lines)
    # Corrupt another occurrence after the already established source/time bracket.
    changed = observed[:-16] + observed[-16:].replace("明月春风", "明夜春风")
    assert repeat_proof(lines, 0, records, records, changed, observed) is None
    context, proof = repeat_proof(
        lines,
        0,
        records,
        records,
        changed,
        observed,
        [dict(after_line_index=3, time=7)],
    )
    assert proof["expected_count"] == 1 and proof["bracket_after_line_indices"] == [
        -1,
        3,
    ]


def test_partial_asr_interior_does_not_change_boundary_word_requirement():
    line = Line(id="a", text="纵然青史已经成灰")
    tokens = [
        dict(text=char, start=10 + i * 0.4, end=10 + i * 0.4 + 0.3)
        for i, char in enumerate("纵然情深已经成灰")
    ]
    record = match_boundary_lines([line], tokens)[0]
    assert (
        record["coverage"] == 0.75
        and record["prefix_confirmed"]
        and record["suffix_confirmed"]
    )
    tokens[-1]["text"] = "贵"
    assert not match_boundary_lines([line], tokens)[0]["suffix_confirmed"]


def test_small_cut_error_moves_partition_in_common_gap_without_moving_native_words():
    a = quality(
        Timeline(
            duration=100,
            lines=[Line(id="a", text="甲乙", units=[word("a", "甲乙", 0, 49, 50.12)])],
        )
    )
    b = quality(
        Timeline(
            duration=100,
            lines=[Line(id="b", text="丙丁", units=[word("b", "丙丁", 0, 50.18, 51)])],
        )
    )
    native = a.lines[0].units[0].model_dump()
    left = dict(
        id="0",
        indices=[0],
        core_start=0,
        core_end=50,
        boundary_native_lines=[a.lines[0].model_dump()],
        native_boundary_violations=[
            dict(
                line_id="a",
                unit_id=native["id"],
                start=49,
                end=50.12,
                native_unit=native,
            )
        ],
    )
    right = dict(
        id="1",
        indices=[1],
        core_start=50,
        core_end=100,
        boundary_native_lines=[b.lines[0].model_dump()],
        native_boundary_violations=[],
    )
    a.lines[0].units[0].start = a.lines[0].units[0].end = None
    results = [(left, a), (right, b)]
    plan = dict(boundaries=[dict(after_line_index=0, pause_start=49.8, pause_end=50.5)])
    adjustments = repair_seams(results, plan)
    assert adjustments[0]["new_cut"] == 50.15
    assert (a.lines[0].units[0].start, a.lines[0].units[0].end) == (49, 50.12)
    assert right["core_start"] == left["core_end"] == 50.15
    assert seam_groups(results) == [[0], [1]]


def test_actual_tifa_overlap_cannot_be_resolved_by_moving_cut():
    a = Line(id="a", text="甲", units=[word("a", "甲", 0, 49, 50.4)])
    b = Line(id="b", text="乙", units=[word("b", "乙", 0, 50.2, 51)])
    results = [
        (
            dict(
                id="0",
                indices=[0],
                core_start=0,
                core_end=50,
                boundary_native_lines=[a.model_dump()],
                native_boundary_violations=[],
            ),
            Timeline(duration=100, lines=[a]),
        ),
        (
            dict(
                id="1",
                indices=[1],
                core_start=50,
                core_end=100,
                boundary_native_lines=[b.model_dump()],
                native_boundary_violations=[],
            ),
            Timeline(duration=100, lines=[b]),
        ),
    ]
    assert (
        repair_seams(
            results,
            dict(
                boundaries=[dict(after_line_index=0, pause_start=49.8, pause_end=50.6)]
            ),
        )
        == []
    )
    assert seam_groups(results) == [[0, 1]]


def test_one_complete_unique_sentence_can_split_without_recognizing_next_sentence():
    lines = [
        Line(id="a", text="明月照亮远方"),
        Line(id="b", text="未知歌词内容"),
        Line(id="c", text="另一段歌词"),
    ]
    first = match_boundary_lines(
        lines,
        [
            dict(text=c, start=20 + i * 0.5, end=20 + i * 0.5 + 0.4)
            for i, c in enumerate(lines[0].text)
        ],
    )
    assert first[0]["prefix_confirmed"] and first[0]["suffix_confirmed"]
    assert not build_plan(lines, first, first, 0, 100)["boundaries"]
    plan = build_plan(
        lines, first, first, 0, 100, fine_anchors=True, min_seconds=10, max_seconds=45
    )
    assert len(plan["blocks"]) == 2
    assert plan["boundaries"][0]["boundary_kind"] == "complete_sentence_endpoint"
    assert plan["boundaries"][0]["time"] == first[0]["end"]
    assert not plan["boundaries"][0]["confirmed_pause"]
    assert [i for b in plan["blocks"] for i in b["indices"]] == [0, 1, 2]


def test_single_refrain_cannot_prove_which_occurrence_when_other_occurrence_missing():
    lines = [
        Line(id=str(i), text=t)
        for i, t in enumerate(["明月照亮远方", "这是一句过渡", "明月照亮远方", "终曲"])
    ]
    tokens = [
        dict(text=c, start=20 + i * 0.5, end=20 + i * 0.5 + 0.4)
        for i, c in enumerate(lines[0].text)
    ]
    records = match_boundary_lines(lines, tokens)
    text = "".join(match_text(t["text"]) for t in tokens)
    plan = build_plan(
        lines,
        records,
        records,
        0,
        100,
        observed_first=text,
        observed_second=text,
        fine_anchors=True,
        min_seconds=10,
        max_seconds=45,
    )
    assert not plan["boundaries"]


def test_fine_sentence_anchor_requires_independent_time_agreement():
    lines = [Line(id="a", text="明月照亮远方"), Line(id="b", text="未知歌词内容")]
    tokens = [
        dict(text=c, start=20 + i * 0.5, end=20 + i * 0.5 + 0.4)
        for i, c in enumerate(lines[0].text)
    ]
    first = match_boundary_lines(lines, tokens)
    second = match_boundary_lines(
        lines, [dict(t, start=t["start"] + 2, end=t["end"] + 2) for t in tokens]
    )
    assert not build_plan(
        lines, first, second, 0, 100, fine_anchors=True, min_seconds=10, max_seconds=45
    )["boundaries"]


def test_phonetic_spelling_comparison_keeps_whole_token_times_and_display_text():
    line = Line(id="a", text="僕は校歌を歌う", language="ja")
    reading = {
        "僕は校歌を歌う": "ぼくはこうかをうたう",
        "僕": "ぼく",
        "効果": "こうか",
        "歌う": "うたう",
    }
    tokens = [
        dict(text=t, start=10 + i, end=10 + i + 0.8)
        for i, t in enumerate(["僕", "は", "効果", "を", "歌う"])
    ]
    record = match_boundary_lines(
        [line], tokens, reading=lambda text: reading.get(text, text)
    )[0]
    assert (
        strong_match(line, record)
        and record["prefix_confirmed"]
        and record["suffix_confirmed"]
    )
    assert (record["start"], record["end"]) == (10, 14.8)
    assert line.text == "僕は校歌を歌う" and "効果" in record["recognized_text"]


def test_fine_plan_uses_short_blocks_and_caps_context_at_unknown_neighbour():
    lines = source(24)
    tokens = character_tokens(lines)
    records = match_boundary_lines(lines, tokens)
    observed = "".join(match_text(t["text"]) for t in tokens)
    plan = build_plan(
        lines,
        records,
        records,
        0,
        80,
        target=25,
        observed_first=observed,
        observed_second=observed,
        fine_anchors=True,
        min_seconds=10,
        max_seconds=45,
        tail_seconds=8,
    )
    assert all(b["duration"] <= 45 for b in plan["blocks"])
    table = {m["line_index"]: m for m in records[:1]}
    block = dict(indices=[1], core_start=10, core_end=25, context_limit_seconds=6)
    assert context_job(block, lines, table, table, 0, 80)["context_indices"] == [1]


def test_long_risky_chain_cannot_merge_back_to_full_song():
    lines = [Line(id=str(i), text="一句歌词") for i in range(4)]
    results = []
    for i, line in enumerate(lines):
        line.units = [word(line.id, line.text, 0, i * 25 + 1, i * 25 + 3)]
        results.append(
            (
                dict(
                    id=str(i),
                    indices=[i],
                    core_start=i * 25,
                    core_end=(i + 1) * 25,
                    native_boundary_violations=[
                        dict(line_id=line.id, start=i * 25, end=(i + 1) * 25)
                    ],
                ),
                quality(Timeline(duration=100, lines=[line])),
            )
        )
    assert seam_groups(results) == [[0, 1, 2, 3]]
    groups, deferred = bounded_seam_groups(results, lines, {}, {}, 0, 100)
    assert groups == [[0, 1], [2, 3]] and len(deferred) == 1


def test_local_seam_retry_fills_missing_unit_without_changing_native_anchor(
    tmp_path, monkeypatch
):
    import ktv_app.aligners.seam_repair as module

    a = Line(
        id="a", text="甲乙", units=[word("a", "甲", 0, 20, 21), word("a", "乙", 1)]
    )
    b = Line(
        id="b",
        text="丙丁",
        units=[word("b", "丙", 0, 31, 32), word("b", "丁", 1, 33, 34)],
    )
    left = quality(Timeline(duration=60, lines=[a], diagnostics={"unit_sources": {}}))
    right = quality(Timeline(duration=60, lines=[b], diagnostics={"unit_sources": {}}))
    results = [
        (dict(id="0", core_start=0, core_end=30, native_boundary_violations=[]), left),
        (
            dict(id="1", core_start=30, core_end=60, native_boundary_violations=[]),
            right,
        ),
    ]
    monkeypatch.setattr(module, "crop", lambda *args: None)

    def align(audio, selected, duration, origin, *args):
        selected[0].units = [word("new", "甲", 0, 19, 20), word("new", "乙", 1, 22, 23)]
        selected[1].units = [
            word("newb", "丙", 0, 30, 31),
            word("newb", "丁", 1, 32, 33),
        ]
        return quality(
            Timeline(
                duration=duration, lines=selected, diagnostics={"unit_sources": {}}
            )
        )

    monkeypatch.setattr(module, "run_anchor_alignment", align)
    report = retry_seam_gaps(
        tmp_path / "input.wav", results, 60, 0, 60, tmp_path, object(), lambda _: None
    )
    assert report[0]["end"] - report[0]["start"] == 24
    assert left.lines[0].units[0].start == 20 and right.lines[0].units[0].start == 31
    assert left.lines[0].units[1].start == 22 and left.lines[0].units[1].id == "a-1"
    assert left.diagnostics["unit_sources"]["a-1"] == "tifa_local"


def test_seam_retry_cannot_cross_native_anchor_in_following_sentence(
    tmp_path, monkeypatch
):
    import ktv_app.aligners.seam_repair as module

    a = Line(id="a", text="甲", units=[word("a", "甲", 0, 10, 11)])
    b = Line(id="b", text="乙丙", units=[word("b", "乙", 0), word("b", "丙", 1)])
    c = Line(id="c", text="丁", units=[word("c", "丁", 0, 30, 31)])
    left = quality(Timeline(duration=50, lines=[a], diagnostics={"unit_sources": {}}))
    right = quality(
        Timeline(duration=50, lines=[b, c], diagnostics={"unit_sources": {}})
    )
    results = [
        (dict(id="0", core_start=0, core_end=20, native_boundary_violations=[]), left),
        (
            dict(id="1", core_start=20, core_end=50, native_boundary_violations=[]),
            right,
        ),
    ]
    monkeypatch.setattr(module, "crop", lambda *args: None)

    def align(audio, selected, duration, origin, *args):
        selected[0].units = [word("newa", "甲", 0, 10, 11)]
        selected[1].units = [
            word("newb", "乙", 0, 34, 35),
            word("newb", "丙", 1, 36, 37),
        ]
        return quality(
            Timeline(
                duration=duration, lines=selected, diagnostics={"unit_sources": {}}
            )
        )

    monkeypatch.setattr(module, "run_anchor_alignment", align)
    reports = retry_seam_gaps(
        tmp_path / "input.wav", results, 50, 0, 50, tmp_path, object(), lambda _: None
    )
    assert all(unit.start is None for unit in right.lines[0].units)
    assert right.lines[1].units[0].start == 30
    assert not reports[0]["accepted_units"]
    assert len(reports[0]["rejected_units"]) == 2
    assert all(item["following_start"] == 30 for item in reports[0]["rejected_units"])


@pytest.mark.parametrize("policy", ["reliable_blocks_v1", "asr_fine_blocks_v2"])
def test_fine_and_legacy_completion_both_keep_block_ownership(policy):
    timeline = Timeline(
        duration=60,
        lines=[Line(id="a", text="甲乙"), Line(id="b", text="丙丁")],
        diagnostics={
            "block_policy": policy,
            "alignment_blocks": [
                dict(id="0", line_ids=["a"], effective_audio_bounds=[0, 25]),
                dict(id="1", line_ids=["b"], effective_audio_bounds=[25, 60]),
            ],
        },
    )
    result = complete_timeline(timeline, rolling=True)
    assert (
        result.diagnostics["auto_completion"]["policy"]
        == "block_fixed_anchor_gap_interpolation"
    )
    assert result.lines[0].end <= 25 <= result.lines[1].start
