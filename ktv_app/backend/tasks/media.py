"""Media preparation and browser playback."""

from ...media import browser_proxy
from .context import TaskContext


def media(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid

    projects.event(pid, "media_ready", "音频与波形已就绪")
    return


def playback(context: TaskContext) -> None:
    projects = context.projects
    pid = context.pid
    p = context.project
    folder = context.folder

    projects.event(pid, "playback", "生成浏览器兼容播放副本；最终导出仍使用输入原视频")
    proxy = folder / "playback.webm"
    if not proxy.exists():
        browser_proxy(p["video"], proxy, projects.settings)
    projects.update(pid, playback="playback.webm")
    projects.event(pid, "ready", "兼容播放副本已就绪")
    return
