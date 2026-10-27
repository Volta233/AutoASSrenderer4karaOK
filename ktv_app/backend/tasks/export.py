"""Render a saved timeline snapshot and record its artifacts."""

import shutil
import uuid
from pathlib import Path
from ...rendering import render, RenderOptions, video_filename
from ...subtitles import write_bundle
from ...timeline import Line, Timeline, Style, quality
from ..storage import read_json, write_json
from .context import TaskContext


def export(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid
    payload = context.payload
    p = context.project
    folder = context.folder
    info = context.media

    if (folder / "timeline.json").exists():
        timeline = Timeline.model_validate(read_json(folder / "timeline.json"))
    else:
        lines = (
            [Line.model_validate(l) for l in read_json(folder / "lines.json")]
            if (folder / "lines.json").exists()
            else []
        )
        timeline = quality(
            Timeline(
                duration=info["duration"],
                origin=info["audio_offset"],
                lines=lines,
                diagnostics={
                    "warnings": ["尚无已定位歌词，仍输出原视频并保留完整歌词清单"]
                },
            )
        )
    options = RenderOptions.model_validate(payload)
    p["style"] = (
        Style.model_validate(p["style"])
        .model_copy(update={"auto_complete": True, "mode": "double", "lead": 0})
        .model_dump()
    )
    projects.update(pid, style=p["style"])
    projects.event(
        pid,
        "render",
        "烧录动态字幕并封装原始音轨",
    )
    bundle = folder / "renders" / f"r{timeline.revision}_{uuid.uuid4().hex[:8]}"
    write_bundle(timeline, Style.model_validate(p["style"]), bundle)
    export_report = read_json(bundle / "export_report.json")
    for name in (
        "lyrics.original.txt",
        "lyrics.txt",
        "lyrics.source.json",
        "media.json",
        "candidates.json",
    ):
        if (folder / name).exists():
            shutil.copyfile(folder / name, bundle / name)
    if (folder / "alignment").exists():
        shutil.copytree(folder / "alignment", bundle / "alignment")
    # Export contains settings but never API credentials.
    write_json(
        bundle / "project.json",
        {
            k: v
            for k, v in p.items()
            if k not in ("candidates", "timeline", "events", "lyrics", "workflow")
        },
    )
    target = render(
        p["video"],
        bundle,
        info,
        projects.settings,
        options,
        output_name=video_filename(
            p["song"], p["artist"], p.get("video_name") or p["video"]
        ),
    )
    exported = None
    if options.output_dir:
        output = (
            Path(options.output_dir).expanduser().resolve()
            / f"{target.stem}_{bundle.name}"
        )
        shutil.copytree(bundle, output)
        exported = str(output)
    current = projects.get(pid)
    current["artifacts"].append(
        {
            "kind": "export",
            "path": target.relative_to(folder).as_posix(),
            "bundle": bundle.relative_to(folder).as_posix(),
            "revision": timeline.revision,
            "exported": exported,
            "report": export_report,
        }
    )
    projects.update(pid, artifacts=current["artifacts"])
    projects.event(
        pid,
        "ready",
        "MP4、字幕、完整歌词和未匹配清单已导出"
        + f"；显示 {export_report['rendered_lines']}/{export_report['total_lines']} 行，未匹配不阻止输出",
    )
