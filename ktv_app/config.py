import os
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def default_tifa_python():
    portable = (
        ROOT
        / ".runtime/tifa"
        / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    )
    return portable if portable.exists() else sys.executable


def path_setting(name, default):
    value = Path(os.environ.get(name, str(default))).expanduser()
    return (ROOT / value).resolve() if not value.is_absolute() else value.resolve()


@dataclass
class Settings:
    projects: Path = field(
        default_factory=lambda: path_setting("KTV_PROJECTS", ROOT / "ktv_projects")
    )
    tifa_source: Path = field(
        default_factory=lambda: path_setting(
            "TIFA_SOURCE", ROOT / "models/forced_alignment/sources/TIFA"
        )
    )
    tifa_model: Path = field(
        default_factory=lambda: path_setting(
            "TIFA_MODEL", ROOT / "models/forced_alignment/tifa/TIFA-1.0-ST/model.pt"
        )
    )
    tifa_python: Path = field(
        default_factory=lambda: path_setting("TIFA_PYTHON", default_tifa_python())
    )
    g2p: str = field(default_factory=lambda: os.getenv("TIFA_G2P", ""))
    device: str = "cuda"
    cpu_threads: int = field(
        default_factory=lambda: int(os.getenv("KTV_CPU_THREADS", "8"))
    )
    locator_python: Path = field(
        default_factory=lambda: path_setting(
            "KTV_LOCATOR_PYTHON",
            ROOT
            / (
                ".runtime/locator/Scripts/python.exe"
                if os.name == "nt"
                else ".runtime/locator/bin/python"
            ),
        )
    )
    separation_python: Path = field(
        default_factory=lambda: path_setting(
            "KTV_SEPARATION_PYTHON",
            ROOT
            / (
                ".runtime/separation/Scripts/python.exe"
                if os.name == "nt"
                else ".runtime/separation/bin/python"
            ),
        )
    )
    kim_source: Path = field(
        default_factory=lambda: path_setting(
            "KTV_KIM_SOURCE", ROOT / "models/separation/sources/Kim"
        )
    )
    kim_model: Path = field(
        default_factory=lambda: path_setting(
            "KTV_KIM_MODEL",
            ROOT / "models/separation/Kim/MelBandRoformer.ckpt",
        )
    )
    asr_model: Path = field(
        default_factory=lambda: path_setting(
            "KTV_ASR_MODEL", ROOT / "models/Qwen3-ASR-1.7B"
        )
    )
    lyrics_url: str = field(
        default_factory=lambda: os.getenv("KTV_LYRICS_API", "https://lrclib.net/api")
    )

    def __post_init__(self):
        if self.device != "cuda":
            raise ValueError("此版本仅支持 NVIDIA CUDA 推理")
        if not 1 <= self.cpu_threads <= 64:
            raise ValueError("CPU线程数必须在1到64之间")

    def tool(self, name):
        configured = os.getenv("KTV_" + name.upper())
        if configured:
            p = Path(configured).expanduser()
            if not p.is_absolute():
                p = ROOT / p
            if not p.is_file():
                raise FileNotFoundError(f"{name} 配置路径不存在: {p}")
            return str(p.resolve())
        found = shutil.which(name)
        if found:
            return found
        # Optional local FFmpeg installation.
        for base in (ROOT / "tools",):
            found = next(base.glob(f"ffmpeg/**/{name}.exe"), None)
            if found:
                return str(found)
        raise FileNotFoundError(f"缺少 {name}，请安装 FFmpeg 并配置 KTV_{name.upper()}")
