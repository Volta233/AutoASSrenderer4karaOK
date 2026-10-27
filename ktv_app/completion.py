"""Bounded display fallback. Native times are immutable; estimates never imply singing."""

import math
import re

from .timeline import Unit, Line, Timeline, canonical, quality


def _weight(text):
    return max(1, len(canonical(text)))


def _schedule(items, lower, upper, max_rate=None, align_right=False):
    """Fill positive gaps only; leave anchors unchanged even if they conflict."""
    slots = [(x.start, x.end, False) if x.start is not None else None for x in items]
    anchors = [(i, x.start, x.end) for i, x in enumerate(items) if x.start is not None]
    cursor, previous, unresolved = lower, -1, []
    for index, start, end in anchors + [(len(items), upper, upper)]:
        indices = list(range(previous + 1, index))
        if indices:
            if start - cursor < 0.04 * len(indices):
                unresolved.extend(indices)
            else:
                total = sum(_weight(items[i].text) for i in indices)
                span = start - cursor
                if max_rate is not None:
                    span = min(span, max_rate * total)
                left = start - span if align_right and previous == -1 else cursor
                finish = left + span
                for j, i in enumerate(indices):
                    right = (
                        finish
                        if j == len(indices) - 1
                        else left + span * _weight(items[i].text) / total
                    )
                    slots[i] = (left, right, True)
                    left = right
        cursor = max(cursor, end)
        previous = index
    return slots, unresolved


def _make_units(line):
    units = sorted(line.units, key=lambda u: u.source_start)
    additions, cursor = [], 0
    for unit in units + [None]:
        right = unit.source_start if unit else len(line.text)
        for match in re.finditer(
            r"[A-Za-z0-9]+(?:[\x27’][A-Za-z0-9]+)*|[^\W\s]",
            line.text[cursor:right],
            re.UNICODE,
        ):
            a, b = cursor + match.start(), cursor + match.end()
            additions.append(
                Unit(
                    id=f"{line.id}-auto-{a}-{b}",
                    text=line.text[a:b],
                    source_start=a,
                    source_end=b,
                )
            )
        if unit:
            cursor = unit.source_end
    if not units and not additions and line.text:
        additions = [
            Unit(
                id=f"{line.id}-auto-full",
                text=line.text,
                source_start=0,
                source_end=len(line.text),
            )
        ]
    line.units = sorted(units + additions, key=lambda u: u.source_start)


def _estimate(unit, start, end):
    unit.start, unit.end, unit.timing_source = start, end, "estimated"
    unit.phones, unit.readings = [], []


def complete_timeline(
    timeline: Timeline, vocal_start=None, vocal_end=None, rolling=False
):
    if rolling:
        if timeline.diagnostics.get("block_policy") in (
            "reliable_blocks_v1",
            "asr_fine_blocks_v2",
            "progressive_tifa_v1",
        ):
            return complete_block_gaps(timeline, vocal_start, vocal_end)
        if timeline.diagnostics.get("boundary_policy") == "tifa_endpoints_only_v3":
            return complete_anchor_gaps(timeline, vocal_start, vocal_end)
        return complete_rolling(timeline, vocal_start, vocal_end)
    """Preserve native anchors; unresolved edge lyrics become static review cards."""
    result = quality(remove_estimates(timeline))
    if not result.lines:
        return result
    for value in (vocal_start, vocal_end):
        if value is not None and (
            not math.isfinite(value) or not 0 <= value <= result.duration
        ):
            raise ValueError("演唱区间必须在视频内")
    if vocal_start is not None and vocal_end is not None and vocal_end <= vocal_start:
        raise ValueError("演唱结束必须晚于开始")
    anchors = [u for l in result.lines for u in l.units if u.start is not None]
    lower = (
        vocal_start
        if vocal_start is not None
        else min((u.start for u in anchors), default=result.duration * 0.8)
    )
    upper = (
        vocal_end
        if vocal_end is not None
        else max((u.end for u in anchors), default=result.duration)
    )
    if lower >= upper:
        raise ValueError("演唱区间与已有时间冲突，请同时填写正确的开始和结束")
    schedule, deferred = _schedule(result.lines, lower, upper)
    originals, notes = {}, []
    if deferred:
        review_lower = max(
            max((u.end for u in anchors), default=0), result.duration * 0.8
        )
        if result.duration - review_lower < 0.04 * len(deferred):
            review_lower = max(
                lower,
                result.duration
                - max(1, min(result.duration * 0.2, len(deferred) * 0.5)),
            )
        span = result.duration - review_lower
        total = sum(_weight(result.lines[i].text) for i in deferred)
        cursor = review_lower
        for j, i in enumerate(deferred):
            end = (
                result.duration
                if j == len(deferred) - 1
                else cursor + span * _weight(result.lines[i].text) / total
            )
            schedule[i] = (cursor, end, True)
            cursor = end
    no_evidence = not anchors and vocal_start is None
    for index, (line, (start, end, estimated_line)) in enumerate(
        zip(result.lines, schedule)
    ):
        changed = (
            estimated_line or not line.units or any(u.start is None for u in line.units)
        )
        if changed:
            originals[line.id] = line.model_dump()
        line.display_kind = "review" if index in deferred or no_evidence else "karaoke"
        if estimated_line:
            line.completion_note = (
                "没有可约束的演唱区间：在末尾作为未定位歌词卡片显示，不代表此处演唱"
                if line.display_kind == "review"
                else "仅在已定位相邻行或用户指定演唱区间内估算；静态显示，不模拟逐字演唱"
            )
        _make_units(line)
        slots, unknown = _schedule(line.units, start, end)
        for ui, unit in enumerate(line.units):
            if ui in unknown:
                _estimate(unit, start, end)
            elif slots[ui][2]:
                _estimate(unit, slots[ui][0], slots[ui][1])
        if changed:
            notes.append(
                {
                    "line_id": line.id,
                    "start": start,
                    "end": end,
                    "display_kind": line.display_kind,
                    "reason": line.completion_note
                    or "缺失字词静态显示；模型边界保持原值",
                }
            )
    result = quality(result)
    result.diagnostics["auto_completion"] = {
        "enabled": True,
        "policy": "bounded_static_fallback_preserve_native",
        "vocal_start": vocal_start,
        "vocal_end": vocal_end,
        "effective_start": lower,
        "effective_end": upper,
        "total_lines": len(result.lines),
        "output_lines": sum(l.start is not None for l in result.lines),
        "estimated_lines": sum(
            any(u.timing_source == "estimated" for u in l.units) for l in result.lines
        ),
        "estimated_units": sum(
            u.timing_source == "estimated" for l in result.lines for u in l.units
        ),
        "review_cards": sum(l.display_kind == "review" for l in result.lines),
        "original_lines": originals,
        "changes": notes,
        "note": "估算只保证文字输出，不证明演唱；估算字词保持静态。无可靠边界的行作为末尾复核卡片，已有模型和手动时间不重排。",
    }
    return result


def complete_rolling(timeline, vocal_start=None, vocal_end=None):
    """All production rows sweep. Retain estimates and original evidence separately.

    The static legacy mode is retained for reading historical callers; the service
    explicitly chooses this mode. Edge estimates extrapolate a bounded lyric rate,
    rather than filling the entire intro/outro or collecting missing rows at the end.
    """
    import statistics

    result = quality(remove_estimates(timeline))
    if not result.lines:
        return result
    for value in (vocal_start, vocal_end):
        if value is not None and (
            not math.isfinite(value) or not 0 <= value <= result.duration
        ):
            raise ValueError("演唱区间必须在视频内")
    if vocal_start is not None and vocal_end is not None and vocal_end <= vocal_start:
        raise ValueError("演唱结束必须晚于开始")
    originals = {l.id: l.model_dump() for l in result.lines}
    rates = [
        (l.end - l.start)
        / max(1, sum(len(canonical(u.text)) for u in l.units if u.start is not None))
        for l in result.lines
        if l.start is not None and 0.04 < l.end - l.start < 15
    ]
    rate = max(0.08, min(0.45, statistics.median(rates) if rates else 0.2))
    # Select non-conflicting native line anchors, reserving a positive interval
    # for intervening missing occurrences. Rejected evidence stays in originals.
    fixed = []
    previous_end = result.origin
    previous_index = -1
    for i, line in enumerate(result.lines):
        if line.start is None:
            continue
        native = [u for u in line.units if u.start is not None]
        implausible = (
            line.end - line.start > max(15, _weight(line.text) * 0.65 + 3)
            or any(u.end - u.start > 8 for u in native)
            or any(
                y.start - x.end > 3 or y.start < x.end - 0.15
                for x, y in zip(native, native[1:])
            )
        )
        if (
            implausible
            or line.start - previous_end < 0.02 * (i - previous_index - 1)
            or line.end - line.start < 0.02
        ):
            for unit in line.units:
                unit.start = unit.end = None
                unit.phones = []
                unit.readings = []
            line.start = line.end = None
            continue
        fixed.append((i, line))
        previous_end = line.end
        previous_index = i
    first = fixed[0][0] if fixed else len(result.lines)
    last = fixed[-1][0] if fixed else -1
    prefix = sum(_weight(l.text) for l in result.lines[:first]) * rate
    suffix = sum(_weight(l.text) for l in result.lines[last + 1 :]) * rate
    if fixed:
        first_native = next(u for u in fixed[0][1].units if u.start is not None)
        last_native = next(
            u for u in reversed(fixed[-1][1].units) if u.start is not None
        )
        prefix += len(canonical(fixed[0][1].text[: first_native.source_start])) * rate
        suffix += len(canonical(fixed[-1][1].text[last_native.source_end :])) * rate
        lower = (
            vocal_start
            if vocal_start is not None
            else max(result.origin, fixed[0][1].start - prefix)
        )
        upper = (
            vocal_end
            if vocal_end is not None
            else min(result.duration, fixed[-1][1].end + suffix)
        )
        lower = min(lower, fixed[0][1].start)
        upper = max(upper, fixed[-1][1].end)
    else:
        # No acoustic evidence: explicit estimates over the chosen interval.
        lower = vocal_start if vocal_start is not None else max(0, result.origin)
        upper = vocal_end if vocal_end is not None else result.duration
    had_native = any(
        u.get("start") is not None for l in originals.values() for u in l["units"]
    )
    schedule, unresolved = _schedule(
        result.lines,
        lower,
        upper,
        max_rate=max(0.15, min(0.6, rate * 1.5)) if fixed or had_native else None,
        align_right=bool(fixed),
    )
    # Even zero-space edge cases get a visible sweep, without masquerading as a
    # reliable model boundary. Only such impossible cases may overlap an anchor.
    for i in unresolved:
        line = result.lines[i]
        before = next(
            (l for l in reversed(result.lines[:i]) if l.end is not None), None
        )
        after = next((l for l in result.lines[i + 1 :] if l.start is not None), None)
        a = before.end if before else lower
        b = after.start if after else upper
        if b - a < 0.02:
            a = max(0, min(a, result.duration - 0.04))
            b = min(result.duration, a + max(0.04, min(2, _weight(line.text) * rate)))
        schedule[i] = (a, b, True)
    notes = []
    row_end = lower
    for li, (line, slot) in enumerate(zip(result.lines, schedule)):
        a, b, estimated = slot
        line.display_kind = "karaoke"
        _make_units(line)
        native = [u for u in line.units if u.start is not None]
        if native:
            prefix = (
                _weight(line.text[: native[0].source_start])
                if canonical(line.text[: native[0].source_start])
                else 0
            )
            suffix = (
                _weight(line.text[native[-1].source_end :])
                if canonical(line.text[native[-1].source_end :])
                else 0
            )
            a = max(row_end, lower, min(a, native[0].start - prefix * rate))
            following = schedule[li + 1][0] if li + 1 < len(schedule) else upper
            b = min(following, upper, max(b, native[-1].end + suffix * rate))
            # Native evidence stays fixed even if a supplied bound is wrong.
            a = min(a, native[0].start)
            b = max(b, native[-1].end)
        slots, unknown = _schedule(
            line.units,
            a,
            b,
            max_rate=None if estimated else max(0.15, min(0.6, rate * 1.5)),
            align_right=True,
        )
        if unknown:
            # Intra-line collisions cannot be repaired by assigning all missing
            # words the same interval. Rebuild the entire line as marked estimates.
            span = max(0.001, b - a)
            weight = sum(_weight(u.text) for u in line.units)
            cursor = a
            for j, u in enumerate(line.units):
                end = (
                    b
                    if j == len(line.units) - 1
                    else cursor + span * _weight(u.text) / weight
                )
                _estimate(u, cursor, end)
                cursor = end
            estimated = True
        else:
            for u, s in zip(line.units, slots):
                if s[2]:
                    _estimate(u, s[0], s[1])
        if estimated or any(u.timing_source == "estimated" for u in line.units):
            line.completion_note = "缺失时间按相邻歌词锚点和字词权重估算，逐字滚动；需要试听校对，不计入模型覆盖率"
            notes.append(
                {
                    "line_id": line.id,
                    "start": a,
                    "end": b,
                    "reason": line.completion_note,
                }
            )
        row_end = max((u.end for u in line.units if u.start is not None), default=b)
    result = quality(result)
    result.diagnostics["auto_completion"] = {
        "enabled": True,
        "policy": "bounded_rolling_estimates",
        "vocal_start": vocal_start,
        "vocal_end": vocal_end,
        "effective_start": lower,
        "effective_end": upper,
        "total_lines": len(result.lines),
        "output_lines": sum(l.start is not None for l in result.lines),
        "estimated_lines": sum(
            any(u.timing_source == "estimated" for u in l.units) for l in result.lines
        ),
        "estimated_units": sum(
            u.timing_source == "estimated" for l in result.lines for u in l.units
        ),
        "review_cards": 0,
        "original_lines": originals,
        "changes": notes,
        "no_acoustic_anchors": not bool(fixed),
        "note": "每句均生成滚动时间；补齐时间是估算，不代表模型识别成功。原始证据可恢复，时间准确度需人工核验。",
    }
    return result


def complete_block_gaps(timeline, vocal_start=None, vocal_end=None):
    """Missing words never borrow time across a verified audio/lyric partition."""
    result = remove_estimates(timeline)
    by_id = {line.id: line for line in result.lines}
    originals = {line.id: line.model_dump() for line in result.lines}
    changes, released, unresolved, block_reports = [], [], [], []
    completed = {}
    for block in result.diagnostics["alignment_blocks"]:
        a, b = block["effective_audio_bounds"]
        selected = [by_id[lid].model_copy(deep=True) for lid in block["line_ids"]]
        sub = Timeline(
            duration=result.duration,
            origin=result.origin,
            lines=selected,
            diagnostics={
                "boundary_policy": "tifa_endpoints_only_v3",
                "effective_audio_bounds": [a, b],
            },
        )
        start = (
            vocal_start if vocal_start is not None and a <= vocal_start < b else None
        )
        end = vocal_end if vocal_end is not None and a < vocal_end <= b else None
        filled = complete_anchor_gaps(sub, start, end)
        completed.update({line.id: line for line in filled.lines})
        report = filled.diagnostics["auto_completion"]
        block_reports.append(
            {
                "block_id": block["id"],
                "start": a,
                "end": b,
                "no_acoustic_anchors": report["no_acoustic_anchors"],
                "estimated_lines": report["estimated_lines"],
                "estimated_units": report["estimated_units"],
            }
        )
        for destination, key in (
            (changes, "changes"),
            (released, "released_conflicting_anchors"),
            (unresolved, "unresolved"),
        ):
            destination.extend(
                dict(value, block_id=block["id"]) for value in report[key]
            )
    assert set(completed) == set(by_id)
    result.lines = [completed[line.id] for line in result.lines]
    result = quality(result)
    result.diagnostics["auto_completion"] = {
        "enabled": True,
        "policy": "block_fixed_anchor_gap_interpolation",
        "original_lines": originals,
        "vocal_start": vocal_start,
        "vocal_end": vocal_end,
        "changes": changes,
        "released_conflicting_anchors": released,
        "unresolved": unresolved,
        "block_reports": block_reports,
        "total_lines": len(result.lines),
        "output_lines": sum(line.start is not None for line in result.lines),
        "estimated_lines": sum(
            any(u.timing_source == "estimated" for u in line.units)
            for line in result.lines
        ),
        "estimated_units": sum(
            u.timing_source == "estimated" for line in result.lines for u in line.units
        ),
        "review_cards": 0,
        "note": "每块单独在固定词之间补齐；不跨已核验切点借时间。",
    }
    return result


def complete_anchor_gaps(timeline, vocal_start=None, vocal_end=None):
    """Interpolate missing source runs only; never rebuild a healthy whole line."""
    from .aligners.anchor_policy import (
        scaffold,
        missing_runs,
        lyric_rate,
        clear,
        endpoints,
    )

    result = quality(remove_estimates(timeline))
    if not result.lines:
        return result
    for value in (vocal_start, vocal_end):
        if value is not None and (
            not math.isfinite(value) or not 0 <= value <= result.duration
        ):
            raise ValueError("演唱区间必须在视频内")
    if vocal_start is not None and vocal_end is not None and vocal_end <= vocal_start:
        raise ValueError("演唱结束必须晚于开始")
    originals = {l.id: l.model_dump() for l in result.lines}
    for line in result.lines:
        scaffold(line)
    rate = lyric_rate(result)
    released = []
    # A zero-space missing occurrence cannot be displayed without a conflict.
    # Release only the conflicting neighbour, retain its original evidence, and
    # widen the missing run; never compress several missing rows into a few frames.
    for _ in range(sum(len(l.units) for l in result.lines) + 1):
        conflict = None
        for run in missing_runs(result):
            before, after = run["before"], run["after"]
            chars = sum(_weight(u.text) for _, u in run["items"])
            whole_rows = {
                li
                for li, _ in run["items"]
                if all(u.start is None for u in result.lines[li].units)
            }
            crosses_lines = len({li for li, _ in run["items"]}) > 1 or bool(whole_rows)
            per_char = max(
                [0.06]
                + [
                    0.3 / max(1, len(canonical(result.lines[li].text)))
                    for li in whole_rows
                ]
            )
            minimum = chars * per_char if crosses_lines else 0.005
            if before and after and after.start - before.end < minimum:
                conflict = (run, minimum)
                break
        if conflict is None:
            break
        run, minimum = conflict
        neighbour = (
            run["after"] if run["after"].timing_source != "manual" else run["before"]
        )
        if neighbour.timing_source == "manual":
            break
        released.append(
            {
                "unit_id": neighbour.id,
                "text": neighbour.text,
                "start": neighbour.start,
                "end": neighbour.end,
                "reason": "no_space_between_anchors",
                "required_seconds": minimum,
            }
        )
        clear(neighbour)
    native = [u for l in result.lines for u in l.units if u.start is not None]
    flat = [u for l in result.lines for u in l.units]
    first = next((i for i, u in enumerate(flat) if u.start is not None), len(flat))
    last = next(
        (i for i in range(len(flat) - 1, -1, -1) if flat[i].start is not None), -1
    )
    audio_lower, audio_upper = result.diagnostics.get(
        "effective_audio_bounds", [result.origin, result.duration]
    )
    if native:
        lower = (
            vocal_start
            if vocal_start is not None
            else max(
                audio_lower,
                native[0].start - sum(_weight(u.text) for u in flat[:first]) * rate,
            )
        )
        upper = (
            vocal_end
            if vocal_end is not None
            else min(
                audio_upper,
                native[-1].end + sum(_weight(u.text) for u in flat[last + 1 :]) * rate,
            )
        )
    else:
        lower = vocal_start if vocal_start is not None else audio_lower
        upper = vocal_end if vocal_end is not None else audio_upper
    changes = []
    unresolved = []
    for run in missing_runs(result):
        before, after = run["before"], run["after"]
        a = before.end if before else lower
        b = after.start if after else upper
        chars = sum(_weight(u.text) for _, u in run["items"])
        reason = "between_fixed_neighbour_words"
        if b <= a:
            unresolved.append(
                {
                    "line_ids": list(
                        dict.fromkeys(result.lines[li].id for li, _ in run["items"])
                    ),
                    "reason": "manual_anchor_conflict",
                    "start": a,
                    "end": b,
                }
            )
            continue
        # Only long instrumental/unknown edge gaps get a bounded extrapolation.
        # Ordinary gaps use the complete previous.end -> next.start interval.
        if native and b - a > max(8, chars * 1.2):
            b = a + min(b - a, chars * rate)
            reason = "long_gap_bounded_after_failed_local_retry"
        cursor = a
        for j, (li, u) in enumerate(run["items"]):
            end = (
                b
                if j == len(run["items"]) - 1
                else cursor + (b - a) * _weight(u.text) / chars
            )
            _estimate(u, cursor, end)
            cursor = end
        changes.append(
            {
                "line_ids": list(
                    dict.fromkeys(result.lines[li].id for li, _ in run["items"])
                ),
                "start": a,
                "end": b,
                "characters": chars,
                "word_count": len(run["items"]),
                "reason": reason,
            }
        )
    for line in result.lines:
        line.display_kind = "karaoke"
        if any(u.timing_source == "estimated" for u in line.units):
            line.completion_note = "仅缺失词在固定相邻锚点之间按有效字符数插值；首尾不足时先合并重试。估算需试听。"
    result = quality(result)
    # After filling all words, these are exactly first source word.start and last.end.
    for line in result.lines:
        a, b = endpoints(line)
        if a is not None and b is not None and a < b:
            line.start, line.end = a, b
    result.diagnostics["auto_completion"] = {
        "enabled": True,
        "policy": "fixed_anchor_gap_interpolation",
        "original_lines": originals,
        "vocal_start": vocal_start,
        "vocal_end": vocal_end,
        "effective_start": lower,
        "effective_end": upper,
        "lyric_rate": rate,
        "changes": changes,
        "released_conflicting_anchors": released,
        "unresolved": unresolved,
        "total_lines": len(result.lines),
        "output_lines": sum(l.start is not None for l in result.lines),
        "estimated_lines": sum(
            any(u.timing_source == "estimated" for u in l.units) for l in result.lines
        ),
        "estimated_units": sum(
            u.timing_source == "estimated" for l in result.lines for u in l.units
        ),
        "review_cards": 0,
        "no_acoustic_anchors": not bool(native),
        "note": "有效原生时间不重排；单个缺词取前词结束到后词开始，连续缺词按字符数分配。",
    }
    return result


def remove_estimates(timeline: Timeline):
    result = timeline.model_copy(deep=True)
    originals = result.diagnostics.get("auto_completion", {}).get("original_lines", {})
    for index, line in enumerate(result.lines):
        saved = originals.get(line.id)
        if (
            saved
            and saved["text"] == line.text
            and all(u.timing_source == "estimated" for u in line.units)
        ):
            result.lines[index] = Line.model_validate(saved)
            continue
        for ui, unit in enumerate(line.units):
            if unit.timing_source == "estimated":
                prior = (
                    next(
                        (u for u in saved.get("units", []) if u["id"] == unit.id), None
                    )
                    if saved and saved["text"] == line.text
                    else None
                )
                if prior:
                    line.units[ui] = Unit.model_validate(prior)
                else:
                    unit.start = unit.end = None
                    unit.phones, unit.readings = [], []
        line.completion_note = ""
        line.display_kind = "karaoke"
    if "auto_completion" in result.diagnostics:
        result.diagnostics["auto_completion"]["enabled"] = False
    return quality(result)
