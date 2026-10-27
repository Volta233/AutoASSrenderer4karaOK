from pathlib import Path
import json

from .timeline import Timeline, Style, canonical
from .completion import remove_estimates


def clock(seconds, lrc=False):
    value = max(0, round(seconds * 100))
    h, rest = divmod(value, 360000)
    m, rest = divmod(rest, 6000)
    s, cs = divmod(rest, 100)
    return (
        f"{h * 60 + m:02d}:{s:02d}.{cs:02d}" if lrc else f"{h}:{m:02d}:{s:02d}.{cs:02d}"
    )


def ass_color(color):
    return "&H00" + color[5:7] + color[3:5] + color[1:3]


def escape(text):
    return (
        text.replace("\\", "＼")
        .replace("{", "｛")
        .replace("}", "｝")
        .replace("\n", "\\N")
    )


def karaoke(line, event_start, style=None, event_end=None, warnings=None):
    style = style or Style()
    warnings = warnings if warnings is not None else []
    result, cursor, pos = [], round(event_start * 100), 0
    limit = round(event_end * 100) if event_end is not None else 10**12

    def static(display):
        # Unknown words have zero duration and stay in the base colour throughout.
        return (
            "{\\k0\\1c"
            + ass_color(style.base)
            + "\\2c"
            + ass_color(style.base)
            + "}"
            + escape(display)
            + "{\\1c"
            + ass_color(style.highlight)
            + "\\2c"
            + ass_color(style.base)
            + "}"
        )

    for unit in line.units:
        display = line.text[pos : unit.source_end]
        pos = unit.source_end
        if unit.start is None:
            result.append(static(display))
            continue
        a, b = round(unit.start * 100), round(unit.end * 100)
        a, b = max(cursor, a), min(limit, b)
        if a != round(unit.start * 100) or b != round(unit.end * 100):
            warnings.append(
                {
                    "line_id": line.id,
                    "unit_id": unit.id,
                    "reason": "显示时裁剪重叠或超出本行的边界；原时间轴保持不变",
                }
            )
        if b <= a:
            result.append(static(display))
            continue
        if a > cursor:
            # Zero-width karaoke syllable holds sweep through real inter-word pauses.
            result.append("{\\k" + str(a - cursor) + "}\u200b")
        result.append("{\\kf" + str(max(1, b - a)) + "}" + escape(display))
        cursor, pos = b, unit.source_end
    if pos < len(line.text):
        result.append("{\\kf0}" + escape(line.text[pos:]))
    return "".join(result)


def sweep_midpoint(line):
    """Time when half the lyric characters have swept, excluding inter-word pauses."""
    target = len(canonical(line.text)) / 2
    for unit in line.units:
        if unit.start is None:
            continue
        before = len(canonical(line.text[: unit.source_start]))
        weight = len(canonical(line.text[unit.source_start : unit.source_end]))
        if weight and before <= target <= before + weight:
            return unit.start + (unit.end - unit.start) * (target - before) / weight
    return (line.start + line.end) / 2


def display_windows(timeline, style):
    active = [
        (i, l)
        for i, l in enumerate(timeline.lines)
        if l.start is not None and l.units and l.display_kind != "review"
    ]
    result = {}
    if style.mode == "double":
        starts = []
        for j, (i, line) in enumerate(active):
            start = (
                max(0, active[0][1].start - style.lead)
                if j < 2
                else max(0, min(line.start, sweep_midpoint(active[j - 1][1])))
            )
            if j and line.start - active[j - 1][1].end > 4:
                start = max(start, line.start - 2)
            starts.append(start)
        for j, (i, line) in enumerate(active):
            end = (
                starts[j + 2]
                if j + 2 < len(active)
                else (
                    max(line.end, sweep_midpoint(active[j + 1][1]))
                    if j + 1 < len(active)
                    else line.end + style.tail
                )
            )
            if j + 1 < len(active) and active[j + 1][1].start - line.end > 4:
                end = min(end, line.end + max(0.5, style.tail))
            if (
                j + 2 < len(active)
                and active[j + 2][1].start - active[j + 1][1].end > 4
            ):
                end = min(
                    end, max(line.end, active[j + 1][1].end) + max(0.5, style.tail)
                )
            result[line.id] = {
                "start": starts[j],
                "end": min(timeline.duration, end),
                "slot": j % 2,
                "sweep_start": line.start,
                "sweep_end": line.end,
                "midpoint": sweep_midpoint(line),
            }
    else:
        cursor = 0
        for j, (i, line) in enumerate(active):
            start = max(cursor, max(0, line.start - style.lead))
            end = min(
                timeline.duration,
                line.end + style.tail,
                active[j + 1][1].start if j + 1 < len(active) else timeline.duration,
            )
            result[line.id] = {
                "start": start,
                "end": end,
                "slot": 0,
                "sweep_start": line.start,
                "sweep_end": line.end,
                "midpoint": sweep_midpoint(line),
            }
            cursor = end
    for line in timeline.lines:
        if line.display_kind == "review" and line.start is not None and line.units:
            result[line.id] = {"start": line.start, "end": line.end, "slot": "review"}
    return result


def write_bundle(timeline: Timeline, style: Style, folder, width=1280, height=720):
    # Step 4 never creates or changes timestamps. Keep the saved revision in the bundle.
    source_timeline = timeline
    if not style.auto_complete:
        timeline = remove_estimates(timeline)
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    # Fixed virtual canvas scales consistently to any output resolution via libass.
    font = style.font.replace(",", " ").replace("\n", "").replace("\r", "")
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: 1280
PlayResY: 720
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: KTV,{font},{style.size},{ass_color(style.highlight)},{ass_color(style.base)},&H00121212,&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline},1,2,30,30,35,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events, lrc, unmatched, warnings = [], [], [], []
    windows = display_windows(timeline, style)
    for index, line in enumerate(timeline.lines):
        missing = [u.model_dump() for u in line.units if u.start is None]
        if missing or not line.units:
            unmatched.append(
                {
                    "line_id": line.id,
                    "text": line.text,
                    "entire_line": line.start is None,
                    "units": missing,
                    "reason": "没有可靠时间；保留文字供校对，不推算演唱时间",
                }
            )
        if line.start is None or not line.units:
            continue
        review = line.display_kind == "review"
        window = windows[line.id]
        slot = window["slot"]
        start, end = window["start"], window["end"]
        if end <= start:
            # Retain text as a static review overlay, without retiming native units.
            warnings.append(
                {
                    "line_id": line.id,
                    "reason": "显示边界冲突，本行静态显示供复核；已保存时间戳不变",
                }
            )
            start, end, review = line.start, line.end, True
        if start > line.start + 0.01 or end < line.end - 0.01:
            warnings.append(
                {"line_id": line.id, "reason": "相邻行冲突，裁剪显示区间；可继续导出"}
            )
        y = (
            100
            if review
            else round(
                720 * style.position
                - ((1 - slot) * (style.size * 1.4) if style.mode == "double" else 0)
            )
        )
        content = (
            "{\\1c"
            + ass_color(style.base)
            + "\\2c"
            + ass_color(style.base)
            + "}"
            + escape("未定位 · " + line.text)
            if review
            else karaoke(line, start, style, end, warnings)
        )
        text = "{\\an2\\pos(640," + str(y) + ")}" + content
        events.append(f"Dialogue: 0,{clock(start)},{clock(end)},KTV,,0,0,0,,{text}")
        words = "".join(
            (
                f"<{clock(u.start, True)}>{line.text[(line.units[i - 1].source_end if i else 0) : u.source_end]}<{clock(u.end, True)}>"
                if u.start is not None
                else line.text[
                    (line.units[i - 1].source_end if i else 0) : u.source_end
                ]
            )
            for i, u in enumerate(line.units)
        )
        if line.units[-1].source_end < len(line.text):
            words += line.text[line.units[-1].source_end :]
        lrc.append(f"[{clock(line.start, True)}]{words}")
    (folder / "lyrics.ass").write_text(
        header + "\n".join(events) + "\n", encoding="utf-8-sig"
    )
    (folder / "lyrics.lrc").write_text("\n".join(lrc) + "\n", encoding="utf-8")
    (folder / "timeline.json").write_text(
        source_timeline.model_dump_json(indent=2), encoding="utf-8"
    )
    (folder / "style.json").write_text(
        style.model_dump_json(indent=2), encoding="utf-8"
    )
    (folder / "lyrics.full.txt").write_text(
        "\n".join(l.text for l in timeline.lines) + "\n", encoding="utf-8"
    )
    (folder / "unmatched.json").write_text(
        json.dumps(unmatched, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (folder / "unmatched.txt").write_text(
        "\n".join(row["text"] for row in unmatched) + "\n", encoding="utf-8"
    )
    (folder / "display_plan.json").write_text(
        json.dumps(windows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    report = {
        "policy": "saved_timestamps_rolling_estimates"
        if style.auto_complete
        else "saved_timestamps_hide_estimates",
        "double_line_turnover": "replace_previous_at_next_lyric_half_sweep"
        if style.mode == "double"
        else None,
        "timeline_revision": source_timeline.revision,
        "review_cards": sum(l.display_kind == "review" for l in timeline.lines),
        "auto_complete": style.auto_complete,
        "estimated_lines": sum(
            any(u.timing_source == "estimated" and u.start is not None for u in l.units)
            for l in timeline.lines
        ),
        "estimated_units": sum(
            u.timing_source == "estimated" and u.start is not None
            for l in timeline.lines
            for u in l.units
        ),
        "total_lines": len(timeline.lines),
        "rendered_lines": len(events),
        "timed_units": sum(
            u.start is not None for l in timeline.lines for u in l.units
        ),
        "untimed_units": sum(u.start is None for l in timeline.lines for u in l.units),
        "unmatched_lines": len(unmatched),
        "warnings": warnings,
        "no_timed_subtitles": not bool(events),
        "note": (
            "使用第三步已保存的时间戳；估算字词也滚动，但不计入模型覆盖率。双行在下一句高亮过半时替换上一句。导出不执行补齐或对齐。"
            if style.auto_complete
            else "未匹配不会阻止输出；完全未定位的行不出现在视频中，部分未定位文字保持未唱颜色。原始时间轴与完整歌词均已保存。"
        ),
    }
    (folder / "export_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    estimated = [
        {
            "line_id": l.id,
            "text": l.text,
            "note": l.completion_note,
            "units": [
                u.model_dump()
                for u in l.units
                if u.timing_source == "estimated" and u.start is not None
            ],
        }
        for l in timeline.lines
        if any(u.timing_source == "estimated" and u.start is not None for u in l.units)
    ]
    (folder / "estimated.json").write_text(
        json.dumps(estimated, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return folder / "lyrics.ass"
