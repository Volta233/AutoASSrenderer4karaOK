import math
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Phone(BaseModel):
    text: str
    start: float
    end: float


class Unit(BaseModel):
    id: str
    text: str
    normalized: str = ""
    timing_source: Literal["model", "manual", "estimated"] = "model"
    source_start: int
    source_end: int
    start: float | None = None
    end: float | None = None
    readings: list[Phone] = Field(default_factory=list)
    phones: list[Phone] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class Line(BaseModel):
    id: str
    text: str
    language: str = "zh"
    normalized: str = ""
    source_line: int = 0
    normalization_changes: list[dict] = Field(default_factory=list)
    completion_note: str = ""
    display_kind: Literal["karaoke", "review"] = "karaoke"
    start: float | None = None
    end: float | None = None
    units: list[Unit] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class Timeline(BaseModel):
    schema_version: str = "1.0"
    duration: float
    origin: float = 0
    revision: int = 0
    model: str = "tifa"
    lines: list[Line] = Field(default_factory=list)
    diagnostics: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounds(self):
        if not math.isfinite(self.duration) or self.duration <= 0:
            raise ValueError("媒体时长必须为正数")
        if not math.isfinite(self.origin):
            raise ValueError("音频偏移必须为有限数值")
        ids = set()
        for line in self.lines:
            for item in (line, *line.units):
                if item.id in ids:
                    raise ValueError("时间轴 ID 重复")
                ids.add(item.id)
                a, b = item.start, item.end
                if (a is None) != (b is None):
                    raise ValueError("起止时间必须同时填写或同时留空")
                if a is not None and (
                    not math.isfinite(a)
                    or not math.isfinite(b)
                    or not 0 <= a < b <= self.duration + 0.02
                ):
                    raise ValueError(f"时间超出视频或起止颠倒: {item.id}")
                if isinstance(
                    item, Unit
                ) and not 0 <= item.source_start < item.source_end <= len(line.text):
                    raise ValueError("显示文字映射范围错误")
                if (
                    isinstance(item, Unit)
                    and item.text != line.text[item.source_start : item.source_end]
                ):
                    raise ValueError(
                        "显示文字与原始歌词映射不一致，请重新对齐修改后的歌词"
                    )
        return self


class Style(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["single", "double"] = "double"
    auto_complete: bool = True
    font: str = "Microsoft YaHei"
    size: int = Field(default=48, ge=12, le=160)
    base: str = Field(default="#FFFFFF", pattern=r"^#[0-9a-fA-F]{6}$")
    highlight: str = Field(default="#FFD53D", pattern=r"^#[0-9a-fA-F]{6}$")
    position: float = Field(default=0.86, ge=0.1, le=0.95)
    lead: float = Field(default=0, ge=0, le=10)
    tail: float = Field(default=0.5, ge=0, le=5)
    outline: int = Field(default=3, ge=0, le=10)


def canonical(text):
    return "".join(
        c for c in unicodedata.normalize("NFKC", text).casefold() if c.isalnum()
    )


def quality(timeline):
    previous = -1.0
    for line in timeline.lines:
        line.flags = [
            f
            for f in line.flags
            if f
            in (
                "low_model_support",
                "asr_unmatched",
                "alignment_failed",
                "partial_asr_match",
            )
        ]
        for u in line.units:
            u.flags = [
                f
                for f in u.flags
                if f in ("unmatched_text", "cross_line_word", "boundary_limited")
            ]
            if u.timing_source == "estimated" and "estimated_time" not in u.flags:
                u.flags.append("estimated_time")
            if u.start is None:
                u.flags.append("missing_time")
                continue
            if u.timing_source != "estimated" and u.start < previous - 0.02:
                u.flags.append("overlap_or_drift")
            if u.end - u.start < 0.04:
                u.flags.append("too_short")
            if u.end - u.start > 4:
                u.flags.append("long_sustain_review")
            if u.timing_source != "estimated":
                previous = max(previous, u.end)
        valid = [u for u in line.units if u.start is not None]
        if valid:
            line.start, line.end = (
                min(u.start for u in valid),
                max(u.end for u in valid),
            )
        else:
            line.start = line.end = None
        if any(u.flags for u in line.units) or not line.units:
            line.flags.append("needs_review")
        if any(u.timing_source == "estimated" for u in line.units):
            line.flags.append("auto_estimated")
    return timeline
