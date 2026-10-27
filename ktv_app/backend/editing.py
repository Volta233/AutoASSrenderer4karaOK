"""Lyric and timeline edits, revision checks and undo history."""

import shutil
from typing import TYPE_CHECKING

from ..lyrics import preprocess
from ..text_normalization import clean_lyric_text, normalize_lyrics
from ..timeline import Timeline, quality
from .storage import read_json, write_json

if TYPE_CHECKING:
    from .service import Projects


class ProjectEditor:
    def __init__(self, projects: "Projects"):
        self.projects = projects

    def set_lyrics(self, pid, text=None, candidate=None):
        with self.projects.lock:
            p = self.projects.idle(pid)
            folder = self.projects.folder(pid)
            source = {"provider": "manual", "note": "用户导入/粘贴；未由网络来源确认"}
            if not candidate and (folder / "lyrics.source.json").exists():
                prior = read_json(folder / "lyrics.source.json")
                source["derived_from"] = prior.get("derived_from", prior)
            if candidate:
                options = p.get("candidates", {}).get("candidates", [])
                chosen = next((c for c in options if c["id"] == candidate), None)
                if not chosen:
                    raise ValueError("候选不存在")
                text = chosen["text"]
                source = {
                    k: v for k, v in chosen.items() if k not in ("text", "synced")
                }
            text = clean_lyric_text(text or "")
            lines = preprocess(text, p["language"])
            (folder / "lyrics.original.txt").write_text(text, encoding="utf-8")
            (folder / "lyrics.txt").write_text(
                "\n".join(l.text for l in lines), encoding="utf-8"
            )
            write_json(folder / "lyrics.source.json", source)
            write_json(folder / "lines.json", [l.model_dump() for l in lines])
            (folder / "lyrics_review.json").unlink(missing_ok=True)
            if p.get("timeline"):
                t = Timeline(
                    duration=p["timeline"]["duration"],
                    origin=p["timeline"]["origin"],
                    lines=lines,
                )
                self.save_timeline(pid, t, p["revision"])
            self.projects.update(pid, stage="lyrics_ready", error=None, artifacts=[])
        return self.projects.get(pid)

    def save_timeline(self, pid, timeline, revision, remember=True):
        with self.projects.lock:
            p = self.projects.idle(pid)
            if revision != p["revision"]:
                raise ValueError("时间轴已更新，请刷新后再保存")
            folder = self.projects.folder(pid)
            timeline = quality(Timeline.model_validate(timeline.model_dump()))
            for line in timeline.lines:
                line.normalized, line.normalization_changes = normalize_lyrics(
                    line.text
                )
            if p.get("timeline"):
                old = {u["id"]: u for l in p["timeline"]["lines"] for u in l["units"]}
                for line in timeline.lines:
                    for u in line.units:
                        previous = old.get(u.id)
                        if previous and (u.start, u.end) != (
                            previous["start"],
                            previous["end"],
                        ):
                            u.timing_source = "manual"
                            u.phones = []
                            u.readings = []
            # The media clock is immutable through the editing API.
            timeline = quality(timeline)
            if p.get("media") and (
                abs(timeline.duration - p["media"]["duration"]) > 0.001
                or abs(timeline.origin - p["media"]["audio_offset"]) > 0.001
            ):
                raise ValueError("编辑不能修改媒体时长或原始音频偏移")
            current = folder / "timeline.json"
            history = folder / "history"
            history.mkdir(exist_ok=True)
            if remember and current.exists():
                shutil.copyfile(current, history / f"{p['revision']:08d}.json")
            timeline.revision = p["revision"] + 1
            write_json(current, timeline.model_dump())
            write_json(folder / "lines.json", [l.model_dump() for l in timeline.lines])
            (folder / "lyrics.txt").write_text(
                "\n".join(l.text for l in timeline.lines), encoding="utf-8"
            )
            self.projects.update(
                pid, revision=timeline.revision, error=None, stage="edited"
            )
        return self.projects.get(pid)

    def undo(self, pid):
        with self.projects.lock:
            p = self.projects.idle(pid)
            files = sorted((self.projects.folder(pid) / "history").glob("*.json"))
            if not files:
                raise ValueError("没有可撤销的修改")
            old = Timeline.model_validate(read_json(files[-1]))
            result = self.save_timeline(pid, old, p["revision"], remember=False)
            files[-1].unlink()
            return result

    def persist_alignment(self, pid, timeline):
        folder = self.projects.folder(pid)
        with self.projects.lock:
            current = self.projects.get(pid)
            path = folder / "timeline.json"
            if path.exists():
                history = folder / "history"
                history.mkdir(exist_ok=True)
                shutil.copyfile(path, history / f"{current['revision']:08d}.json")
            timeline.revision = current["revision"] + 1
            write_json(path, timeline.model_dump())
            write_json(folder / "lines.json", [l.model_dump() for l in timeline.lines])
            self.projects.update(pid, revision=timeline.revision)
