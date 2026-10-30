"""Media preparation and browser playback."""

from pathlib import Path

from ...media import browser_proxy, still_video
from ...bilibili import download_video
from .context import TaskContext


def ensure_source_video(projects, context: TaskContext) -> None:
    """Compose an audio/background-image project into a real video before extraction.

    Everything after this point reads ``project["video"]`` as usual, so audio input
    needs no separate branch in probing, alignment, playback or export.
    """
    source = context.project.get("media_source") or {}
    if source.get("kind") == "bilibili":
        download_video(
            source,
            context.folder,
            projects.settings,
            lambda message: projects.event(context.pid, "download", message),
        )
        return
    if source.get("kind") != "audio_image":
        return
    target = Path(context.project["video"])
    if target.is_file() and target.stat().st_size:
        return
    canvas = source.get("canvas") or (None, None)
    projects.event(
        context.pid,
        "media",
        "按背景图与音频合成视频画面；后续步骤与直接上传视频一致",
    )
    still_video(
        source["audio"],
        source["background"],
        target,
        projects.settings,
        canvas[0],
        canvas[1],
    )


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
