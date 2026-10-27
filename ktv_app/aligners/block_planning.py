"""Pure cut planning, relaxation and bounded context windows; no inference."""

import math
from collections import Counter
from .recognition import match_text
from .block_policy import PROFILE, RELAXATION_RULES
from .boundary_matching import (
    match_boundary_lines,
    strong_match,
    unique_context,
    repeat_proof,
    complete_line_cut,
    partial_unique_cut,
)


def build_plan(
    lines,
    first_matches,
    second_matches,
    lower,
    upper,
    target=50,
    observed_first=None,
    observed_second=None,
    fine_anchors=False,
    min_seconds=28,
    max_seconds=62,
    tail_seconds=15,
    source_keys=None,
    relaxation_level=0,
    preserved_candidates=None,
    mandatory_boundaries=None,
):
    rules = RELAXATION_RULES[relaxation_level]
    first = {m["line_index"]: m for m in first_matches}
    second = {m["line_index"]: m for m in second_matches}
    candidates, rejected = [], []
    source_keys = source_keys or [match_text(line.text) for line in lines]
    for index in range(len(lines) - 1):
        records = [
            table.get(j) for table in (first, second) for j in (index, index + 1)
        ]
        reason = None

        def boundary_observed(table):
            left, right = table.get(index), table.get(index + 1)
            if not left or not right:
                return False
            if (
                left.get("method")
                == "exact_character_sequence_with_timed_boundary_words"
            ):
                return left["suffix_confirmed"] and right["prefix_confirmed"]
            return strong_match(lines[index], left) and strong_match(
                lines[index + 1], right
            )

        if not all(boundary_observed(table) for table in (first, second)):
            reason = "insufficient_complete_line_evidence"
        elif (
            abs(first[index]["end"] - second[index]["end"])
            > rules["boundary_difference"]
            or abs(first[index + 1]["start"] - second[index + 1]["start"])
            > rules["boundary_difference"]
        ):
            reason = "shifted_pass_timestamp_disagreement"
        context = (
            unique_context(lines, index, first, second, source_keys, rules)
            if reason is None
            else None
        )
        context_proof = {"method": "unique_source_context"}
        if reason is None and context is None:
            repeated = repeat_proof(
                lines,
                index,
                first,
                second,
                observed_first,
                observed_second,
                source_keys=source_keys,
                rules=rules,
            )
            if repeated:
                context, proof = repeated
                context_proof = dict(
                    proof, method="same_phrase_count_and_occurrence_in_both_passes"
                )
        if reason is None and context is None:
            reason = "repeated_or_unverified_context"
        if reason is None:
            left_end = max(first[index]["end"], second[index]["end"])
            right_start = min(first[index + 1]["start"], second[index + 1]["start"])
            if right_start - left_end < rules["minimum_gap"]:
                reason = "no_confirmed_sentence_gap"
            elif not lower < (left_end + right_start) / 2 < upper:
                reason = "outside_audio"
        if reason:
            rejected.append({"after_line_index": index, "reason": reason})
            continue
        candidates.append(
            {
                "after_line_index": index,
                "time": round((left_end + right_start) / 2, 4),
                "pause_start": left_end,
                "pause_end": right_start,
                "context_indices": context,
                "context_proof": context_proof,
                "two_pass_evidence": records,
            }
        )
    # Repeat counts can also be verified inside previously confirmed anchors.
    # This prevents one recognition error elsewhere from invalidating every
    # occurrence, without letting an uncertain cut justify itself.
    for _ in range(len(lines)):
        additions = []
        for record in rejected:
            if record["reason"] != "repeated_or_unverified_context":
                continue
            index = record["after_line_index"]
            repeated = repeat_proof(
                lines,
                index,
                first,
                second,
                observed_first,
                observed_second,
                candidates,
                source_keys,
                rules,
            )
            if not repeated:
                continue
            context, proof = repeated
            left_end = max(first[index]["end"], second[index]["end"])
            right_start = min(first[index + 1]["start"], second[index + 1]["start"])
            if (
                right_start - left_end < rules["minimum_gap"]
                or not lower < (left_end + right_start) / 2 < upper
            ):
                record["reason"] = "no_confirmed_sentence_gap"
                continue
            additions.append(
                {
                    "after_line_index": index,
                    "time": round((left_end + right_start) / 2, 4),
                    "pause_start": left_end,
                    "pause_end": right_start,
                    "context_indices": context,
                    "context_proof": dict(
                        proof,
                        method="same_phrase_count_and_order_between_confirmed_anchors",
                    ),
                    "two_pass_evidence": [
                        table[j]
                        for table in (first, second)
                        for j in (index, index + 1)
                    ],
                }
            )
        if not additions:
            break
        candidates.extend(additions)
        accepted = {c["after_line_index"] for c in additions}
        rejected = [r for r in rejected if r["after_line_index"] not in accepted]
    candidates.sort(key=lambda c: c["after_line_index"])
    if fine_anchors:
        certified = {cut["after_line_index"] for cut in candidates}
        for index in range(len(lines) - 1):
            if index in certified:
                continue
            cut = complete_line_cut(
                lines,
                index,
                first,
                second,
                source_keys,
                observed_first,
                observed_second,
                relaxation_level,
            )
            if cut and lower < cut["time"] < upper:
                candidates.append(cut)
                certified.add(index)
        rejected = [r for r in rejected if r["after_line_index"] not in certified]
        candidates.sort(key=lambda c: c["after_line_index"])
    candidates = [dict(cut, verification_level=relaxation_level) for cut in candidates]
    by_index = {cut["after_line_index"]: cut for cut in candidates}
    for cut in preserved_candidates or []:
        by_index[cut["after_line_index"]] = cut
    candidates = sorted(by_index.values(), key=lambda cut: cut["after_line_index"])
    certified = set(by_index)
    rejected = [r for r in rejected if r["after_line_index"] not in certified]
    # Keep already selected stronger cuts fixed and add cuts inside their blocks.
    chosen, start, row = [], lower, 0
    for stop in sorted(
        mandatory_boundaries or [], key=lambda c: c["after_line_index"]
    ) + [dict(time=upper, after_line_index=len(lines) - 1)]:
        while stop["time"] - start > max_seconds:
            available = [
                c
                for c in candidates
                if row <= c["after_line_index"] < stop["after_line_index"]
                and c["time"] - start >= min_seconds
                and stop["time"] - c["time"] >= tail_seconds
            ]
            if not available:
                break
            nearby = [c for c in available if c["time"] - start <= max_seconds]
            selected = (
                min(nearby, key=lambda c: abs(c["time"] - start - target))
                if nearby
                else min(available, key=lambda c: c["time"])
            )
            chosen.append(selected)
            start, row = selected["time"], selected["after_line_index"] + 1
        if stop["after_line_index"] < len(lines) - 1:
            chosen.append(stop)
            start, row = stop["time"], stop["after_line_index"] + 1
    blocks, row, start = [], 0, lower
    for cut in chosen + [dict(time=upper, after_line_index=len(lines) - 1)]:
        end_row = cut["after_line_index"] + 1
        indices = list(range(row, end_row))
        blocks.append(
            {
                "id": str(len(blocks)),
                "indices": indices,
                "line_ids": [lines[i].id for i in indices],
                "core_start": start,
                "core_end": cut["time"],
                "duration": round(cut["time"] - start, 4),
                "over_target": cut["time"] - start > max_seconds,
                "reason": "no_reliable_cut_near_target"
                if cut["time"] - start > max_seconds
                else "verified_boundaries",
                **(
                    {"context_limit_seconds": 6, "max_core_seconds": max_seconds}
                    if fine_anchors
                    else {}
                ),
            }
        )
        row, start = end_row, cut["time"]
    assert [i for block in blocks for i in block["indices"]] == list(range(len(lines)))
    return {
        "policy": PROFILE if fine_anchors else "reliable_blocks_v1",
        "target_seconds": target,
        "soft_range_seconds": [min_seconds, max_seconds],
        "fine_sentence_anchors": fine_anchors,
        "boundaries": chosen,
        "candidate_boundaries": candidates,
        "rejected_boundaries": rejected,
        "blocks": blocks,
        "matched_lines_first_pass": len(first),
        "matched_lines_second_pass": len(second),
        "note": "双窗口核验词间边界；细分还允许归属明确的完整句子独立提供句末切点。重复句仍核对次数与顺序。不是人工真值，无可信切点不虚构对应关系。",
    }


def build_adaptive_plan(
    lines,
    tokens,
    verification_tokens,
    lower,
    upper,
    reading=None,
    event=lambda message: None,
):
    """Try strict planning then at most two relaxations if blocks < ceil(minutes)."""
    transform = lambda text: match_text(reading(text) if reading else text)
    keys = [transform(line.text) for line in lines]
    observed = [
        "".join(transform(t["text"]) for t in stream)
        for stream in (tokens, verification_tokens)
    ]
    minimum_blocks = max(1, math.ceil((upper - lower) / 60))
    first = match_boundary_lines(lines, tokens, lower, reading)
    second = match_boundary_lines(lines, verification_tokens, lower, reading)
    strict_complete = [
        sum(
            strong_match(lines[m["line_index"]], m)
            and m["prefix_confirmed"]
            and m["suffix_confirmed"]
            for m in records
        )
        for records in (first, second)
    ]
    plan = None
    attempts = []
    for level in range(3):
        if level:
            event(
                f"切片数 {len(plan['blocks'])} 少于分钟数目标 {minimum_blocks}，执行第 {level}/2 次条件放宽；使用原双遍 ASR，不虚构词时间"
            )
            first = match_boundary_lines(
                lines, tokens, lower, reading, endpoint_chars=1
            )
            second = match_boundary_lines(
                lines, verification_tokens, lower, reading, endpoint_chars=1
            )
        plan = build_plan(
            lines,
            first,
            second,
            lower,
            upper,
            target=25,
            observed_first=observed[0],
            observed_second=observed[1],
            fine_anchors=True,
            min_seconds=10,
            max_seconds=45,
            tail_seconds=8,
            source_keys=keys,
            relaxation_level=level,
            preserved_candidates=plan["candidate_boundaries"] if plan else None,
            mandatory_boundaries=plan["boundaries"] if plan else None,
        )
        attempts.append(
            dict(
                level=level,
                rules=dict(RELAXATION_RULES[level]),
                candidates=len(plan["candidate_boundaries"]),
                blocks=len(plan["blocks"]),
                selected_cuts=len(plan["boundaries"]),
                rejected_reasons=dict(
                    Counter(r["reason"] for r in plan["rejected_boundaries"])
                ),
            )
        )
        if len(plan["blocks"]) >= minimum_blocks:
            break
    plan["adaptive_relaxation"] = dict(
        policy="minute_count_two_stage_v1",
        duration_seconds=upper - lower,
        minimum_blocks=minimum_blocks,
        max_relaxations=2,
        relaxations_used=len(attempts) - 1,
        target_met=len(plan["blocks"]) >= minimum_blocks,
        attempts=attempts,
        boundary_levels=dict(
            Counter(str(c["verification_level"]) for c in plan["boundaries"])
        ),
        note="块数按有效音轨分钟数向上取整触发；最多放宽两次。保留已选强切点，双遍有效词时间、原歌词归属和重复段次序约束保持。",
    )
    plan["complete_timed_lines"] = strict_complete
    return plan, first, second


def frozen_boundaries(plan, max_seconds):
    """Pick the cuts that may stay fixed for the next relaxation round.

    A block that overruns makes its opening cut suspect: the cut was chosen
    when weaker evidence was all that existed, and a better cut inside the
    block only becomes verifiable by a later relaxation. Freezing the old cut
    forever leaves the overrun in place, because new cuts can only be added
    after it, never replace it. So the opening cut goes back into competition
    while every other cut stays nailed down.
    """
    if not plan:
        return None
    reopened = {
        block["indices"][0] - 1
        for block in plan["blocks"]
        if block["duration"] >= max_seconds
    }
    return [
        cut for cut in plan["boundaries"] if cut["after_line_index"] not in reopened
    ]


def build_duration_limited_plan(
    lines,
    tokens,
    verification_tokens,
    lower,
    upper,
    reading=None,
    event=lambda message: None,
    max_input_seconds=90,
):
    """Opt-in experiment, stopping on actual input lengths, not block count."""
    transform = lambda text: match_text(reading(text) if reading else text)
    keys = [transform(line.text) for line in lines]
    observed = [
        "".join(transform(t["text"]) for t in stream)
        for stream in (tokens, verification_tokens)
    ]
    plan = None
    attempts = []
    for level in range(6):
        endpoint_chars = 2 if level == 0 else 1
        first = match_boundary_lines(lines, tokens, lower, reading, endpoint_chars)
        second = match_boundary_lines(
            lines, verification_tokens, lower, reading, endpoint_chars
        )
        by_first = {m["line_index"]: m for m in first}
        by_second = {m["line_index"]: m for m in second}
        candidates = list(plan["candidate_boundaries"]) if plan else []
        if level >= 3:
            certified = {cut["after_line_index"] for cut in candidates}
            for index in range(len(lines) - 1):
                if index in certified:
                    continue
                cut = partial_unique_cut(
                    lines,
                    index,
                    by_first,
                    by_second,
                    keys,
                    observed,
                    level,
                    allow_repeated=level >= 5,
                )
                if cut:
                    cut["context_proof"]["text_basis"] = (
                        "source_text_or_japanese_dictionary_readings"
                    )
                if cut and lower < cut["time"] < upper:
                    candidates.append(cut)
        plan = build_plan(
            lines,
            first,
            second,
            lower,
            upper,
            target=25,
            observed_first=observed[0],
            observed_second=observed[1],
            fine_anchors=True,
            min_seconds=10,
            max_seconds=45,
            tail_seconds=8,
            source_keys=keys,
            relaxation_level=min(level, 2),
            preserved_candidates=candidates,
            mandatory_boundaries=frozen_boundaries(plan, max_input_seconds),
        )
        for block in plan["blocks"]:
            if block["duration"] < max_input_seconds:
                block["context_limit_seconds"] = min(
                    6, max(0, (max_input_seconds - 0.01 - block["duration"]) / 2)
                )
        inputs = [
            dict(id=b["id"], **context_job(b, lines, by_first, by_second, lower, upper))
            for b in plan["blocks"]
        ]
        longest = max(job["audio_end"] - job["audio_start"] for job in inputs)
        attempts.append(
            dict(
                level=level,
                candidates=len(plan["candidate_boundaries"]),
                blocks=len(plan["blocks"]),
                selected_cuts=len(plan["boundaries"]),
                max_core_seconds=max(b["duration"] for b in plan["blocks"]),
                max_input_seconds=longest,
                target_met=longest < max_input_seconds,
                inputs=inputs,
                rules=dict(RELAXATION_RULES[level])
                if level < 3
                else dict(
                    sentence_coverage=0.45 if level == 3 else 0.2,
                    sentence_similarity=0.5 if level == 3 else 0.3,
                    unique_phrase_chars=2,
                    phrase_time_disagreement=0.9,
                    suffix_time_disagreement=1.5,
                    both_passes=True,
                    occurrence_counts_and_order_required=True,
                    repeated_phrase_allowed=level >= 5,
                ),
            )
        )
        event(
            f"核验切片：第 {level + 1} 轮，{len(plan['blocks'])} 块，最长实际输入 {longest:.2f} 秒"
        )
        if longest < max_input_seconds:
            break
    plan["adaptive_relaxation"] = dict(
        policy="actual_input_duration_experiment_v1",
        duration_seconds=upper - lower,
        minimum_blocks=max(1, math.ceil((upper - lower) / 60)),
        max_relaxations=5,
        relaxations_used=len(attempts) - 1,
        target_met=attempts[-1]["target_met"],
        max_input_seconds=max_input_seconds,
        attempts=attempts,
        boundary_levels=dict(
            Counter(str(c["verification_level"]) for c in plan["boundaries"])
        ),
        note="实验选项：最多五次放宽，部分短语双遍核验；重复短语需同次数、同次序。含上下文的实际TIFA输入全部严格小于上限才达标。长度达标不代表歌词时间正确。",
    )
    return plan, first, second


def context_job(block, lines, first, second, lower, upper):
    indices = block["indices"]
    lo, hi = indices[0], indices[-1] + 1
    a, b = block["core_start"], block["core_end"]
    limit = block.get("context_limit_seconds")
    if lo > 0 and lo - 1 in first and lo - 1 in second:
        proposed = max(
            lower,
            min(
                first[lo - 1].get("context_start", first[lo - 1]["start"]),
                second[lo - 1].get("context_start", second[lo - 1]["start"]),
            )
            - 0.4,
        )
        if proposed <= a and (limit is None or a - proposed <= limit):
            a = proposed
            lo -= 1
    if hi < len(lines) and hi in first and hi in second:
        proposed = min(
            upper,
            max(
                first[hi].get("context_end", first[hi]["end"]),
                second[hi].get("context_end", second[hi]["end"]),
            )
            + 0.4,
        )
        if proposed >= b and (limit is None or proposed - b <= limit):
            b = proposed
            hi += 1
    return {"audio_start": a, "audio_end": b, "context_indices": list(range(lo, hi))}
