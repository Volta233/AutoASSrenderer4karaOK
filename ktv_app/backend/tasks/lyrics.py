"""Retrieve lyric candidates from public sources."""

from ...lyrics import search
from ..storage import write_json
from .context import TaskContext


def lookup(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid
    p = context.project
    folder = context.folder
    info = context.media

    projects.event(pid, "search", "联网检索真实歌词来源；请选择与视频一致的版本")
    result = search(
        p["song"],
        p["artist"],
        info["duration"],
        projects.settings,
    )
    write_json(folder / "candidates.json", result)
    if not result["candidates"]:
        projects.event(
            pid,
            "choose_lyrics",
            (
                "歌词来源暂不可用，请查看检索记录后重试，或导入 TXT/LRC。"
                if result.get("retrieval_status") == "unavailable"
                else "已查询的来源没有可采用的歌词；可补充歌手、核对歌名后重试，或导入 TXT/LRC。"
            ),
        )
        return
    projects.event(
        pid,
        "choose_lyrics",
        "请选择与视频一致的歌词版本，或粘贴/导入歌词。",
    )
    return
