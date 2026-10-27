from typing import Protocol
from pathlib import Path
from ..timeline import Timeline, Line


class Aligner(Protocol):
    def align(
        self, audio: Path, lines: list[Line], duration: float, origin: float, work: Path
    ) -> Timeline: ...


def get_aligner(name, settings):
    if name == "tifa":
        from .tifa import TIFAAdapter

        return TIFAAdapter(settings)
    raise ValueError(f"未安装对齐 Adapter: {name}；请实现 Aligner 接口并注册")
