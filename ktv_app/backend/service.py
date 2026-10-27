"""Application service: project views, task preconditions and a serial queue.

Persistence lives in repository/storage, editing in editing, and long-running
work in tasks. HTTP routes call this facade; task handlers never call routes.
"""

from concurrent.futures import ThreadPoolExecutor

from ..lyrics import candidate_evidence
from ..text_normalization import clean_lyric_text
from ..workflow import AlignmentOptions, steps
from .editing import ProjectEditor
from .repository import ProjectRepository
from .storage import read_json, write_json
from .tasks import execute

# Preserve the small public persistence API used by existing integrations.
__all__ = ["Projects", "read_json", "write_json"]


class Projects:
    def __init__(self, settings, recover_interrupted=True):
        self.settings = settings
        self.repository = ProjectRepository(settings.projects, recover_interrupted)
        self.root = self.repository.root
        self.lock = self.repository.lock
        self.editor = ProjectEditor(self)
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="ktv")

    def folder(self, pid):
        return self.repository.folder(pid)

    def create(self, video, song, artist, language, source=None):
        return self.repository.create(video, song, artist, language, source)

    def list(self):
        return self.repository.list()

    def update(self, pid, **fields):
        return self.repository.update(pid, **fields)

    def event(self, pid, stage, message):
        return self.repository.event(pid, stage, message)

    def set_lyrics(self, pid, text=None, candidate=None):
        return self.editor.set_lyrics(pid, text, candidate)

    def save_timeline(self, pid, timeline, revision, remember=True):
        return self.editor.save_timeline(pid, timeline, revision, remember)

    def undo(self, pid):
        return self.editor.undo(pid)

    def persist_alignment(self, pid, timeline):
        return self.editor.persist_alignment(pid, timeline)

    def get(self, pid):
        with self.lock:
            folder = self.folder(pid)
            p = read_json(folder / "project.json")
            p["style"].setdefault("auto_complete", True)
            for name in ("media", "candidates", "timeline"):
                path = folder / (name + ".json")
                if path.exists():
                    p[name] = read_json(path)
            if p.get("candidates"):
                for candidate in p["candidates"].get("candidates", []):
                    candidate["text"] = clean_lyric_text(candidate.get("text", ""))
                    candidate.pop("synced", None)
                    candidate.pop("lrc_last_time", None)
            if p.get("media") and p.get("candidates"):
                for candidate in p["candidates"].get("candidates", []):
                    candidate.update(
                        candidate_evidence(
                            p["song"],
                            p["artist"],
                            p["media"]["duration"],
                            {
                                "trackName": candidate["song"],
                                "artistName": candidate["artist"],
                                "duration": candidate.get("duration"),
                                "plainLyrics": candidate.get("text"),
                            },
                        )
                    )
            lyrics_path = folder / "lines.json"
            p["lyrics"] = read_json(lyrics_path) if lyrics_path.exists() else []
            for line in p["lyrics"] + (p.get("timeline") or {}).get("lines", []):
                line.pop("hint", None)
            p["workflow"] = steps(p, bool(p["lyrics"]))
            return p

    def idle(self, pid):
        p = self.get(pid)
        if p["busy"]:
            raise ValueError("该项目正在处理，请完成后编辑")
        return p

    def submit(self, pid, action, payload=None):
        if action not in (
            "media",
            "search",
            "align",
            "segment",
            "complete",
            "export",
            "playback",
        ):
            raise ValueError("未知处理步骤")
        with self.lock:
            p = self.idle(pid)
            if action in (
                "search",
                "align",
                "segment",
                "complete",
                "export",
            ) and not p.get("media"):
                raise ValueError("请先完成第一步的素材准备")
            if action in ("align", "complete") and not p.get("lyrics"):
                raise ValueError("请先在第二步保存歌词")
            if action in ("export",) and not p.get("timeline"):
                raise ValueError("请先在第三步生成时间戳；未匹配不会阻止输出")
            if action in ("align", "complete"):
                parsed = AlignmentOptions.model_validate(payload or {})
                if any(
                    v is not None and v > p["media"]["duration"]
                    for v in (parsed.vocal_start, parsed.vocal_end)
                ):
                    raise ValueError("演唱区间必须在视频内")
                payload = parsed.model_dump()
            if (
                action in ("segment", "complete", "export")
                and not p["workflow"]["steps"][2]["complete"]
            ):
                raise ValueError("请先执行第三步完整对齐流程，再编辑或导出时间戳")
            if action == "search":
                if not p["song"].strip():
                    raise ValueError(
                        "未导入歌词时，请填写歌曲名后查询；也可直接导入 TXT/LRC"
                    )
            self.update(pid, busy=True, stage="queued", error=None, task=action)
            self.pool.submit(self._job, pid, action, payload or {})
        return self.get(pid)

    def _job(self, pid, action, payload):
        try:
            self._execute(pid, action, payload)
        except Exception as e:
            self.event(pid, "failed", str(e))
            self.update(pid, error=str(e))
        finally:
            self.update(pid, busy=False)

    def _execute(self, pid, action, payload):
        execute(self, pid, action, payload)
