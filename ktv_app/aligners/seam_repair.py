"""Repair block seams without moving valid native anchors."""

import json
from pathlib import Path
from ..media import crop
from ..timeline import Line, Unit, quality
from .anchor_policy import endpoints, run_anchor_alignment
from .common import blank
from .block_planning import context_job


def repair_seams(results, plan):
    """Move a cut inside a gap supported by both native TIFA and both ASR passes.

    This changes the partition, never a native word time. If there is no common
    gap, the existing merged retry remains the fallback.
    """
    adjustments = []
    for index in range(1, len(results)):
        left, left_result = results[index - 1]
        right, right_result = results[index]
        previous = Line.model_validate(left["boundary_native_lines"][-1])
        following = Line.model_validate(right["boundary_native_lines"][0])
        end, start = endpoints(previous)[1], endpoints(following)[0]
        cut = next(
            (
                c
                for c in plan["boundaries"]
                if c["after_line_index"] == left["indices"][-1]
            ),
            None,
        )
        if cut is None or end is None or start is None:
            continue
        a, b = max(end, cut["pause_start"]), min(start, cut["pause_end"])
        if b - a < 0.019:
            continue
        old = left["core_end"]
        if a <= old <= b:
            continue
        updated = round((a + b) / 2, 4)
        if abs(updated - old) > 1:
            continue
        left["core_end"] = right["core_start"] = updated
        restored = []
        for block, result in ((left, left_result), (right, right_result)):
            block["duration"] = round(block["core_end"] - block["core_start"], 4)
            block["effective_audio_bounds"] = [block["core_start"], block["core_end"]]
            block["over_target"] = block["duration"] > block.get("max_core_seconds", 62)
            by_id = {
                u.id: (line, i)
                for line in result.lines
                for i, u in enumerate(line.units)
            }
            for violation in block["native_boundary_violations"]:
                native = violation["native_unit"]
                if (
                    block["core_start"]
                    <= native["start"]
                    < native["end"]
                    <= block["core_end"]
                ):
                    line, ui = by_id[native["id"]]
                    line.units[ui] = Unit.model_validate(native)
                    restored.append(native["id"])
            block["remaining_boundary_violations"] = [
                v
                for v in block["native_boundary_violations"]
                if not block["core_start"] <= v["start"] < v["end"] <= block["core_end"]
            ]
            quality(result)
        adjustments.append(
            {
                "left_block": left["id"],
                "right_block": right["id"],
                "old_cut": old,
                "new_cut": updated,
                "native_gap": [end, start],
                "asr_gap": [cut["pause_start"], cut["pause_end"]],
                "restored_native_units": restored,
                "reason": "common_native_tifa_and_two_pass_asr_gap",
            }
        )
    return adjustments


def seam_groups(results):
    groups = [[0]] if results else []
    for index in range(1, len(results)):
        before, after = results[index - 1], results[index]
        a = endpoints(before[1].lines[-1])[1]
        b = endpoints(after[1].lines[0])[0]
        boundary = before[0]["core_end"]
        violations = before[0].get(
            "remaining_boundary_violations", before[0]["native_boundary_violations"]
        ) + after[0].get(
            "remaining_boundary_violations", after[0]["native_boundary_violations"]
        )
        seam_ids = {before[1].lines[-1].id, after[1].lines[0].id}
        risky = any(
            v["line_id"] in seam_ids
            or abs(v["start"] - boundary) < 2
            or abs(v["end"] - boundary) < 2
            for v in violations
        )
        if risky or (a is not None and b is not None and a > b + 0.001):
            groups[-1].append(index)
        else:
            groups.append([index])
    return groups


def retry_seam_gaps(audio, results, duration, origin, limit, work, settings, event):
    """Retry only the two edge sentences in a 24-second window, retaining fixed words."""
    reports = []
    for index in range(1, len(results)):
        left, left_result = results[index - 1]
        right, right_result = results[index]
        edges = [
            (left, left_result, left_result.lines[-1]),
            (right, right_result, right_result.lines[0]),
        ]
        if not any(u.start is None for _, _, line in edges for u in line.units):
            continue
        cut = left["core_end"]
        a = max(origin, cut - 12)
        b = min(limit, cut + 12)
        folder = Path(work) / f"seam_local_{index}"
        folder.mkdir(parents=True, exist_ok=True)
        report = {
            "id": f"seam_local_{index}",
            "start": a,
            "end": b,
            "line_ids": [line.id for _, _, line in edges],
            "accepted_units": [],
            "rejected_units": [],
            "policy": "missing_only_inside_original_core",
        }
        event(f"接缝局部重试 {index}：{a:.2f}–{b:.2f} 秒；保留已有有效词时间")
        try:
            crop(audio, folder / "audio.wav", a - origin, b - origin, settings)
            retry = run_anchor_alignment(
                folder / "audio.wav",
                blank([line for _, _, line in edges]),
                duration,
                a,
                folder,
                settings,
                lambda message: event("接缝 " + str(index) + "：" + message),
            )
            replacements = {line.id: line for line in retry.lines}
            for block, result, line in edges:
                candidate = replacements.get(line.id)
                if candidate is None:
                    continue
                by_span = {
                    (u.source_start, u.source_end, u.text): u
                    for u in candidate.units
                    if u.start is not None
                }
                adopted = []
                for ui, unit in enumerate(line.units):
                    new = by_span.get((unit.source_start, unit.source_end, unit.text))
                    if unit.start is not None or new is None:
                        continue
                    if (
                        not block["core_start"] - 0.02
                        <= new.start
                        < new.end
                        <= block["core_end"] + 0.02
                    ):
                        continue
                    all_lines = [
                        current
                        for _, current_result in results
                        for current in current_result.lines
                    ]
                    location = next(
                        i
                        for i, current in enumerate(all_lines)
                        if current.id == line.id
                    )
                    preceding = [
                        u
                        for current in all_lines[:location]
                        for u in current.units
                        if u.start is not None
                    ]
                    preceding.extend(u for u in line.units[:ui] if u.start is not None)
                    subsequent = [
                        u for u in line.units[ui + 1 :] if u.start is not None
                    ]
                    subsequent.extend(
                        u
                        for current in all_lines[location + 1 :]
                        for u in current.units
                        if u.start is not None
                    )
                    previous = preceding[-1] if preceding else None
                    following = subsequent[0] if subsequent else None
                    if (previous and new.start < previous.end - 0.001) or (
                        following and new.end > following.start + 0.001
                    ):
                        report["rejected_units"].append(
                            {
                                "line_id": line.id,
                                "native_unit": new.model_dump(),
                                "reason": "conflicts_with_adjacent_source_native_anchor",
                                "previous_end": previous.end if previous else None,
                                "following_start": following.start
                                if following
                                else None,
                            }
                        )
                        continue
                    line.units[ui] = new.model_copy(update={"id": unit.id}, deep=True)
                    result.diagnostics["unit_sources"][unit.id] = "tifa_local"
                    adopted.append(unit.id)
                    report["accepted_units"].append(unit.id)
                block["remaining_boundary_violations"] = [
                    v
                    for v in block.get(
                        "remaining_boundary_violations",
                        block["native_boundary_violations"],
                    )
                    if v["unit_id"] not in adopted
                ]
                result.diagnostics.setdefault("seam_local_runs", []).append(
                    dict(report, accepted_units=adopted)
                )
                quality(result)
            report["native"] = (
                retry.diagnostics.get("global_runs", {})
                .get("tifa", {})
                .get("native", [])
            )
        except (RuntimeError, ValueError) as error:
            report["error"] = str(error)[:600]
        (folder / "seam_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        reports.append(report)
    return reports


def bounded_seam_groups(
    results, lines, first, second, origin, limit, max_input_seconds=60
):
    """Do not undo fine partitioning by merging a risky chain into a whole song."""
    groups, deferred = [], []
    for group in seam_groups(results):
        current = [group[0]]
        for index in group[1:]:
            proposed = current + [index]
            subset = [results[i][0] for i in proposed]
            block = {
                "indices": [j for item in subset for j in item["indices"]],
                "core_start": subset[0]["core_start"],
                "core_end": subset[-1]["core_end"],
                "context_limit_seconds": 6,
            }
            context = context_job(block, lines, first, second, origin, limit)
            if context["audio_end"] - context["audio_start"] <= max_input_seconds:
                current = proposed
            else:
                groups.append(current)
                deferred.append(
                    {
                        "left_block": results[current[-1]][0]["id"],
                        "right_block": results[index][0]["id"],
                        "reason": "merged_input_exceeds_60_seconds",
                        "proposed_audio_seconds": context["audio_end"]
                        - context["audio_start"],
                    }
                )
                current = [index]
        groups.append(current)
    return groups, deferred
