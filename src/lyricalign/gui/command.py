from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


def _looks_like_project_root(path: Path) -> bool:
    return (path / "scripts" / "demo" / "run_qwen_fa_batch.py").is_file() and (
        path / "src" / "lyricalign" / "gui" / "app.py"
    ).is_file()


def _candidate_project_roots() -> list[Path]:
    candidates: list[Path] = []
    configured_root = os.environ.get("LYRICALIGN_ROOT")
    if configured_root:
        candidates.append(Path(configured_root))

    executable = Path(sys.executable).resolve()
    candidates.extend([executable.parent, *executable.parents])

    source_file = Path(__file__).resolve()
    candidates.extend([source_file.parent, *source_file.parents])
    candidates.append(Path.cwd())
    return candidates


def find_project_root() -> Path:
    for candidate in _candidate_project_roots():
        if _looks_like_project_root(candidate):
            return candidate
    searched = os.pathsep.join(str(path) for path in _candidate_project_roots())
    raise RuntimeError(
        "Unable to locate the LyricAlignment project root. "
        "Set LYRICALIGN_ROOT to the directory that contains scripts/ and src/. "
        f"Searched: {searched}"
    )


ROOT = find_project_root()
BATCH_SCRIPT = ROOT / "scripts" / "demo" / "run_qwen_fa_batch.py"
DEFAULT_QWEN_MODEL_PATH = ROOT / "models" / "hf" / "Qwen3-ForcedAligner-0.6B-hf"
DEFAULT_R2_CHECKPOINT_PATH = ROOT / "models" / "R2checkpoint"
DEFAULT_PIPELINE_PYTHON_PATH = Path(
    os.environ.get("LYRICALIGN_PIPELINE_PYTHON", r"D:\Anconda\envs\qwen312\python.exe")
)
if not DEFAULT_PIPELINE_PYTHON_PATH.is_file():
    DEFAULT_PIPELINE_PYTHON_PATH = Path(sys.executable)


@dataclass(frozen=True)
class GuiRunConfig:
    media: Path
    lyrics: Path
    python_executable: Path = DEFAULT_PIPELINE_PYTHON_PATH
    output_dir: Path | None = None
    language: str = "Chinese"
    stage: str = "all"
    preset: str = "default"
    separator: str = "demucs"
    render_audio: str = "source"
    font: str = ""
    subtitle_band_height: int | None = None
    karaoke_primary_color: str = ""
    karaoke_secondary_color: str = ""
    subtitle_preview_color: str = ""
    subtitle_outline_color: str = ""
    audio_width: int = 1280
    audio_height: int = 720
    background_image: Path | None = None
    device: str = "cpu"
    demucs_device: str = "cpu"
    demucs_torch_home: Path | None = None
    demucs_command: str = ""
    model: str = str(DEFAULT_QWEN_MODEL_PATH)
    r1_checkpoint: Path | None = None
    r2_checkpoint: Path | None = DEFAULT_R2_CHECKPOINT_PATH
    cache_dir: Path | None = None
    local_files_only: bool = False
    force_prepare: bool = False
    force_separation: bool = False
    force_align: bool = False
    force_render: bool = False
    dry_run: bool = False


def build_batch_command(config: GuiRunConfig) -> list[str]:
    command = [
        str(config.python_executable),
        str(BATCH_SCRIPT),
        str(config.media),
        "--lyrics",
        str(config.lyrics),
        "--language",
        config.language,
        "--stage",
        config.stage,
        "--preset",
        config.preset,
        "--separator",
        config.separator,
        "--render-audio",
        config.render_audio,
        "--device",
        config.device,
    ]
    if config.font.strip():
        command.extend(["--font", config.font.strip()])
    if config.subtitle_band_height is not None:
        command.extend(["--subtitle-band-height", str(config.subtitle_band_height)])
    if config.karaoke_primary_color.strip():
        command.extend(["--karaoke-primary-color", config.karaoke_primary_color.strip()])
    if config.karaoke_secondary_color.strip():
        command.extend(["--karaoke-secondary-color", config.karaoke_secondary_color.strip()])
    if config.subtitle_preview_color.strip():
        command.extend(["--subtitle-preview-color", config.subtitle_preview_color.strip()])
    if config.subtitle_outline_color.strip():
        command.extend(["--subtitle-outline-color", config.subtitle_outline_color.strip()])
    if config.audio_width != 1280:
        command.extend(["--audio-width", str(config.audio_width)])
    if config.audio_height != 720:
        command.extend(["--audio-height", str(config.audio_height)])
    if config.background_image is not None:
        command.extend(["--background-image", str(config.background_image)])
    if config.output_dir is not None:
        command.extend(["--output-dir", str(config.output_dir)])
    if config.separator == "demucs":
        command.extend(["--demucs-device", config.demucs_device])
        if config.demucs_torch_home is not None:
            command.extend(["--demucs-torch-home", str(config.demucs_torch_home)])
        if config.demucs_command.strip():
            command.extend(["--demucs-command", config.demucs_command.strip()])
    if config.model.strip():
        command.extend(["--model", config.model.strip()])
    if config.r1_checkpoint is not None:
        command.extend(["--r1-checkpoint", str(config.r1_checkpoint)])
    if config.r2_checkpoint is not None:
        command.extend(["--r2-checkpoint", str(config.r2_checkpoint)])
    if config.cache_dir is not None:
        command.extend(["--cache-dir", str(config.cache_dir)])
    for enabled, flag in (
        (config.local_files_only, "--local-files-only"),
        (config.force_prepare, "--force-prepare"),
        (config.force_separation, "--force-separation"),
        (config.force_align, "--force-align"),
        (config.force_render, "--force-render"),
        (config.dry_run, "--dry-run"),
    ):
        if enabled:
            command.append(flag)
    return command


def build_child_environment(config: GuiRunConfig) -> dict[str, str]:
    environment = os.environ.copy()
    src = str(ROOT / "src")
    current = environment.get("PYTHONPATH")
    environment["PYTHONPATH"] = src if not current else f"{src}{os.pathsep}{current}"
    if config.demucs_torch_home is not None:
        environment["TORCH_HOME"] = str(config.demucs_torch_home)
    return environment
