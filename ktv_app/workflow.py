"""Step contracts and evidence-based coverage, independent of visual completeness."""

from typing import Literal
import hashlib
import json
from pydantic import BaseModel, ConfigDict, Field, model_validator
from .timeline import canonical


class AlignmentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model: Literal["reliable_blocks_tifa"] = "reliable_blocks_tifa"
    vocal_separation: Literal["kim"] = "kim"
    complete_missing: Literal[True] = True
    max_tifa_input_seconds: Literal[90] = 90
    device: Literal["cuda"] = "cuda"
    vocal_start: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    vocal_end: float | None = Field(default=None, gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def interval(self):
        if (
            self.vocal_start is not None
            and self.vocal_end is not None
            and self.vocal_end <= self.vocal_start
        ):
            raise ValueError("演唱结束必须晚于开始")
        return self


def alignment_identity(project):
    """Identify the confirmed recording and lyrics used by an alignment run."""
    value = {key: project.get(key) for key in ("song", "artist", "language")}
    value["video_sha256"] = project.get("media", {}).get("sha256")
    value["text"] = [line["text"] for line in project.get("lyrics", [])]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


def timing_evidence(timeline):
    counts = dict(model=0, manual=0, estimated=0, missing=0)
    conflict_units = 0
    for line in timeline.get("lines", []):
        source = ["missing"] * len(line["text"])
        rank = {"missing": 0, "estimated": 1, "manual": 2, "model": 3}
        for unit in line.get("units", []):
            if unit["start"] is None:
                continue
            kind = unit.get("timing_source", "model")
            conflict_units += int("overlap_or_drift" in unit.get("flags", []))
            for i in range(unit["source_start"], unit["source_end"]):
                if rank[kind] > rank[source[i]]:
                    source[i] = kind
        for char, kind in zip(line["text"], source):
            counts[kind] += len(canonical(char))
    total = sum(counts.values())
    locator = timeline.get("diagnostics", {}).get("locator", {})
    return {
        "characters": counts,
        "total_characters": total,
        "model_coverage": round(counts["model"] / total, 4) if total else 0,
        "manual_coverage": round(counts["manual"] / total, 4) if total else 0,
        "estimated_coverage": round(counts["estimated"] / total, 4) if total else 0,
        "missing_coverage": round(counts["missing"] / total, 4) if total else 0,
        "strong_asr_lines": locator.get("strong_lines"),
        "partial_asr_lines": locator.get("partial_recovery_lines"),
        "conflicting_units": conflict_units,
        "review_cards": sum(
            l.get("display_kind") == "review" for l in timeline.get("lines", [])
        ),
        "note": "覆盖率按原歌词有效字符计数；估算不计入模型覆盖率。没有人工真值时，不能报告时间准确率或毫秒误差。",
    }


def steps(p, has_lyrics):
    timeline = p.get("timeline")
    done = [
        bool(p.get("media")),
        bool(has_lyrics),
        bool(
            timeline
            and timeline.get("diagnostics", {}).get("step3_completed")
            and p.get("alignment_completed", {}).get("identity")
            == alignment_identity(p)
        ),
        any(
            a["kind"] == "export" and a["revision"] == p["revision"]
            for a in p["artifacts"]
        ),
    ]
    return {
        "steps": [
            {"number": i + 1, "complete": d, "available": i == 0 or done[i - 1]}
            for i, d in enumerate(done)
        ],
        "recommended_step": next((i + 1 for i, d in enumerate(done) if not d), 4),
        "timing_evidence": timing_evidence(timeline) if timeline else None,
    }
