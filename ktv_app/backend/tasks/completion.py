"""Explicit missing-time completion."""

from ...completion import complete_timeline
from ...subtitles import write_bundle
from ...timeline import Line, Timeline, Style
from ..storage import read_json
from .context import TaskContext


def complete(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid
    payload = context.payload
    p = context.project
    folder = context.folder
    info = context.media

    projects.event(
        pid,
        "complete",
        "自动补齐所有缺失行和单元；估算时间明确标记，可直接输出",
    )
    if (folder / "timeline.json").exists():
        timeline = Timeline.model_validate(read_json(folder / "timeline.json"))
    else:
        lines = (
            [Line.model_validate(l) for l in read_json(folder / "lines.json")]
            if (folder / "lines.json").exists()
            else []
        )
        timeline = Timeline(
            duration=info["duration"], origin=info["audio_offset"], lines=lines
        )
    timeline = complete_timeline(
        timeline,
        payload.get("vocal_start"),
        payload.get("vocal_end"),
        rolling=True,
    )
    timeline.diagnostics["step3_completed"] = True
    projects.persist_alignment(pid, timeline)
    style = Style.model_validate(p["style"]).model_copy(update={"auto_complete": True})
    projects.update(pid, style=style.model_dump())
    write_bundle(timeline, style, folder / "subtitles")
    report = timeline.diagnostics.get("auto_completion", {})
    projects.event(
        pid,
        "ready",
        f"已补齐 {len(timeline.lines)}/{len(timeline.lines)} 行；{report.get('estimated_lines', 0)} 行含自动估算时间，可直接导出",
    )
    return
