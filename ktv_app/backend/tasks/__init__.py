"""Dispatch background actions without mixing them with project CRUD."""

from dataclasses import replace

from ...media import extract
from ..storage import read_json
from .context import TaskContext
from . import alignment, completion, export, lyrics, media

HANDLERS = {
    "media": media.media,
    "search": lyrics.lookup,
    "align": alignment.alignment,
    "segment": alignment.alignment,
    "complete": completion.complete,
    "export": export.export,
    "playback": media.playback,
}


def execute(projects, pid: str, action: str, payload: dict) -> None:
    handler = HANDLERS.get(action)
    if handler is None:
        raise ValueError("未知处理步骤")
    context = TaskContext(
        projects, pid, action, payload, projects.get(pid), projects.folder(pid)
    )
    if action == "media" or not (context.folder / "media.json").exists():
        projects.event(pid, "media", "提取原始完整音轨；生成波形并缓存")
        info = extract(context.project["video"], context.folder, projects.settings)
    else:
        info = read_json(context.folder / "media.json")
    context = replace(context, media=info)
    handler(context)
