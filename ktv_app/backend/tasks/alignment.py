"""Kim, Qwen and TIFA alignment workflow."""

import time
import uuid
from ...aligners import get_aligner
from ...aligners.reliable_blocks import align_reliable_blocks
from ...completion import complete_timeline, remove_estimates
from ...media import crop
from ...lyrics import contextual_languages
from ...separation import prepare_alignment_audio
from ...subtitles import write_bundle
from ...timeline import Line, Timeline, Style, quality
from ...workflow import AlignmentOptions, alignment_identity
from ..storage import read_json, write_json
from .context import TaskContext


def alignment(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid
    action = context.action
    payload = context.payload
    p = context.project
    folder = context.folder
    info = context.media

    if not (folder / "lines.json").exists():
        raise ValueError("尚未选择歌词；请先确认候选或人工导入")
    lines = [Line.model_validate(l) for l in read_json(folder / "lines.json")]
    if p["language"] == "auto":
        for line, inferred in zip(lines, contextual_languages(l.text for l in lines)):
            line.language = inferred
    # Rebuild from acoustic evidence, never force-align yesterday's fallback times.
    for line in lines:
        line.completion_note = ""
        line.display_kind = "karaoke"
        line.start = line.end = None
        line.units = []
    origin = info["audio_offset"]
    audio = folder / "audio.wav"
    task_settings = projects.settings
    projects.update(pid, requested_device="cuda")
    audio, audio_evidence = prepare_alignment_audio(
        p["video"],
        audio,
        info,
        folder,
        "kim",
        task_settings,
        lambda message: projects.event(pid, "align", message),
    )
    work = folder / "alignment" / uuid.uuid4().hex[:12]
    work.mkdir(parents=True)
    if action == "segment":
        start, end = float(payload["start"]), float(payload["end"])
        ids = set(payload["line_ids"])
        if (
            not ids
            or not ids <= {l.id for l in lines}
            or not (
                origin
                <= start
                < end
                <= min(info["duration"], origin + info["audio_duration"])
            )
        ):
            raise ValueError("请选择有效的歌词行与音频区间")
        if end - start >= 90:
            raise ValueError("手动重对齐片段也必须小于90秒")
        indices = [i for i, l in enumerate(lines) if l.id in ids]
        if indices != list(range(min(indices), max(indices) + 1)):
            raise ValueError("分段重新对齐必须选择连续歌词行")
        if not (folder / "timeline.json").exists():
            raise ValueError("请先完成一次全曲对齐")
        clip = work / "segment.wav"
        crop(audio, clip, start - origin, end - origin, projects.settings)
        audio, origin = clip, start
        selected = [l for l in lines if l.id in ids]
    else:
        selected = lines
    if action == "segment":
        projects.event(
            pid, "align", "按你选择的歌词和音频区间细对齐；未匹配项不会限制导出"
        )
        try:
            timeline = get_aligner("tifa", task_settings).align(
                audio, selected, info["duration"], origin, work
            )
        except (RuntimeError, ValueError) as error:
            for line in selected:
                line.units = []
                line.start = line.end = None
                line.flags = ["alignment_failed"]
            timeline = quality(
                Timeline(
                    duration=info["duration"],
                    origin=origin,
                    lines=selected,
                    diagnostics={
                        "warnings": [
                            f"片段未完成：{str(error)[:600]}；文字已保留，仍可输出"
                        ]
                    },
                )
            )
    else:
        options = AlignmentOptions.model_validate(payload if action == "align" else {})
        if options.vocal_start is not None or options.vocal_end is not None:
            a = max(
                origin,
                options.vocal_start if options.vocal_start is not None else origin,
            )
            b = min(
                info["duration"],
                origin + info["audio_duration"],
                options.vocal_end
                if options.vocal_end is not None
                else info["duration"],
            )
            if b <= a:
                raise ValueError("所选演唱区间没有音频")
            bounded = work / "vocal_range.wav"
            crop(audio, bounded, a - origin, b - origin, projects.settings)
            audio, origin = bounded, a
        timeline = align_reliable_blocks(
            audio,
            selected,
            info["duration"],
            origin,
            work,
            task_settings,
            lambda message: projects.event(pid, "align", message),
            max_input_seconds=90,
        )
        timeline.model = options.model
        timeline.diagnostics["alignment_options"] = options.model_dump()
        timeline.diagnostics["alignment_audio"] = audio_evidence
    if action == "segment":
        prior = Timeline.model_validate(read_json(folder / "timeline.json"))
        replacements = {l.id: l for l in timeline.lines}
        prior.lines = [replacements.get(l.id, l) for l in prior.lines]
        prior.diagnostics.setdefault("segments", []).append(timeline.diagnostics)
        timeline = quality(prior)
    timeline.origin = info["audio_offset"]
    write_json(work / "timeline.before_completion.json", timeline.model_dump())
    saved_options = (
        payload
        if action == "align"
        else timeline.diagnostics.get("alignment_options", {})
    )
    # A segment can come from a historical CPU timeline; new inference is CUDA.
    options = AlignmentOptions.model_validate({**saved_options, "device": "cuda"})
    timeline = remove_estimates(timeline)
    timeline = complete_timeline(
        timeline, options.vocal_start, options.vocal_end, rolling=True
    )
    p["style"] = (
        Style.model_validate(p["style"])
        .model_copy(update={"auto_complete": True})
        .model_dump()
    )
    projects.update(pid, style=p["style"])
    timeline.diagnostics["step3_completed"] = True
    projects.persist_alignment(pid, timeline)
    if action == "align":
        projects.update(
            pid,
            alignment_completed={
                "identity": alignment_identity(projects.get(pid)),
                "completed_at": time.time(),
                "policy": "fixed_kim_asr_tifa90_v1",
            },
        )
    estimated = sum(
        any(u.timing_source == "estimated" for u in l.units) for l in timeline.lines
    )
    projects.event(
        pid,
        "aligned",
        f"已保存 {sum(l.start is not None for l in timeline.lines)}/{len(timeline.lines)} 行时间；{estimated} 行含自动估算，可直接输出",
    )
    # Only create editable subtitles here. Video export stays under explicit user control.
    bundle = folder / "subtitles"
    write_bundle(timeline, Style.model_validate(p["style"]), bundle)
    projects.event(
        pid, "ready", "第三步完成：时间戳已保存。第四步使用本次结果生成字幕视频"
    )
    return
