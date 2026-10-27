"""On-disk project records, IDs and interrupted-task recovery.

No model imports or background threads belong in this layer.
"""

import threading
import time
import uuid
from pathlib import Path

from ..media import AUDIO_SUFFIXES, IMAGE_SUFFIXES, VIDEO_SUFFIXES
from ..timeline import Style
from .storage import read_json, write_json


class ProjectRepository:
    def __init__(self, root: Path, recover_interrupted: bool = True):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        if recover_interrupted:
            self.recover_interrupted()

    def recover_interrupted(self):
        for path in self.root.glob("*/project.json"):
            project = read_json(path)
            if project.get("busy"):
                project.update(
                    busy=False,
                    stage="interrupted",
                    error="应用关闭时任务中断；中间结果已保留，请重跑对应步骤",
                )
                write_json(path, project)

    def folder(self, pid):
        if len(pid) != 32 or any(c not in "0123456789abcdef" for c in pid):
            raise FileNotFoundError("项目不存在")
        folder = self.root / pid
        if not (folder / "project.json").exists():
            raise FileNotFoundError("项目不存在")
        return folder

    def create(self, video, song, artist, language, source=None):
        """Create a project from a video file or from an audio/background-image pair.

        ``source`` carries the audio-image variant; the derived video is produced
        later by the media step, so the project folder owns that path from the start.
        """
        if language not in ("auto", "zh", "ja", "en"):
            raise ValueError("语言必须是 auto/zh/ja/en")
        if source:
            return self._create_audio_image(source, song, artist, language)
        video = Path(video).expanduser().resolve()
        if not video.is_file() or video.suffix.lower() not in VIDEO_SUFFIXES:
            raise ValueError("请提供存在的 MP4/MKV/MOV/AVI/WebM 视频文件")
        pid = uuid.uuid4().hex
        folder = self.root / pid
        folder.mkdir()
        value = {
            "id": pid,
            "song": song,
            "artist": artist,
            "language": language,
            "video": str(video),
            "video_name": video.name,
            "media_source": {"kind": "video"},
            "created": time.time(),
            "stage": "created",
            "busy": False,
            "revision": 0,
            "events": [],
            "error": None,
            "style": Style().model_dump(),
            "artifacts": [],
        }
        write_json(folder / "project.json", value)
        return value

    def _create_audio_image(self, source, song, artist, language):
        audio = Path(source["audio"]).expanduser().resolve()
        background = Path(source["background"]).expanduser().resolve()
        if not audio.is_file() or audio.suffix.lower() not in AUDIO_SUFFIXES:
            raise ValueError("请提供 MP3/M4A/WAV/FLAC 音频文件")
        if not background.is_file() or background.suffix.lower() not in IMAGE_SUFFIXES:
            raise ValueError("请提供 JPG/PNG/WebP/BMP 背景图")
        canvas = source.get("canvas") or (None, None)
        pid = uuid.uuid4().hex
        folder = self.root / pid
        folder.mkdir()
        value = {
            "id": pid,
            "song": song,
            "artist": artist,
            "language": language,
            "video": str(folder / "video.mp4"),
            "video_name": Path(source.get("name") or audio.name).name,
            "media_source": {
                "kind": "audio_image",
                "audio": str(audio),
                "background": str(background),
                "canvas": [int(canvas[0]), int(canvas[1])] if canvas[0] else None,
                "video": "video.mp4",
            },
            "created": time.time(),
            "stage": "created",
            "busy": False,
            "revision": 0,
            "events": [],
            "error": None,
            "style": Style().model_dump(),
            "artifacts": [],
        }
        write_json(folder / "project.json", value)
        return value

    def list(self):
        with self.lock:
            values = [read_json(path) for path in self.root.glob("*/project.json")]
        return sorted(values, key=lambda p: p["created"], reverse=True)

    def update(self, pid, **fields):
        with self.lock:
            folder = self.folder(pid)
            p = read_json(folder / "project.json")
            p.update(fields)
            write_json(folder / "project.json", p)

    def event(self, pid, stage, message):
        with self.lock:
            folder = self.folder(pid)
            p = read_json(folder / "project.json")
            p["stage"] = stage
            p["events"].append(
                {"time": time.time(), "stage": stage, "message": message}
            )
            p["events"] = p["events"][-200:]
            write_json(folder / "project.json", p)
