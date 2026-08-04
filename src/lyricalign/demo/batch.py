from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

VIDEO_EXTENSIONS: tuple[str, ...] = (
    ".mp4", ".mkv", ".mov", ".webm", ".avi", ".m4v",
)
AUDIO_EXTENSIONS: tuple[str, ...] = (
    ".wav", ".flac", ".mp3", ".m4a", ".aac", ".ogg", ".opus", ".wma",
)
LYRICS_EXTENSIONS: tuple[str, ...] = (".txt",)
MODELS = ("r0", "r1", "r2")
AUDIO_INPUTS = ("mix", "vocal")
ALIGNMENT_MODES = ("full", "windowed")


@dataclass(frozen=True)
class MediaJob:
    stem: str
    parent: Path
    lyrics: Path
    video: Path | None
    audio: Path | None

    @property
    def source_media(self) -> Path:
        return self.video or self.audio  # type: ignore[return-value]

    @property
    def mix_source(self) -> Path:
        # A same-stem sidecar audio file is preferred for alignment/separation,
        # while the video remains the visual source. This avoids re-encoding a
        # potentially higher-quality audio sidecar merely because a video exists.
        return self.audio or self.video  # type: ignore[return-value]


@dataclass(frozen=True, order=True)
class IndividualMode:
    model: str
    audio: str
    mode: str

    @property
    def token(self) -> str:
        return f"{self.model}:{self.audio}:{self.mode}"


@dataclass(frozen=True)
class OutputPlan:
    individuals: tuple[IndividualMode, ...]
    compare_models: tuple[tuple[str, str], ...]
    compare_inputs: tuple[str, ...]

    @property
    def required_models(self) -> tuple[str, ...]:
        names = {item.model for item in self.individuals}
        return tuple(model for model in MODELS if model in names)

    @property
    def required_audio_inputs(self) -> tuple[str, ...]:
        names = {item.audio for item in self.individuals}
        return tuple(audio for audio in AUDIO_INPUTS if audio in names)


def _pick(paths: Iterable[Path], extension_order: Sequence[str]) -> Path | None:
    by_extension: dict[str, list[Path]] = {}
    for path in paths:
        by_extension.setdefault(path.suffix.lower(), []).append(path)
    for extension in extension_order:
        candidates = sorted(by_extension.get(extension, []), key=lambda item: item.name.lower())
        if candidates:
            return candidates[0]
    return None


def _iter_candidate_files(directory: Path, *, recursive: bool) -> Iterable[Path]:
    iterator = directory.rglob("*") if recursive else directory.iterdir()
    for path in iterator:
        if not path.is_file() or path.name.startswith("."):
            continue
        if any("_qwen_fa" in part for part in path.parts):
            continue
        if path.suffix.lower() in VIDEO_EXTENSIONS + AUDIO_EXTENSIONS + LYRICS_EXTENSIONS:
            yield path


def _group_directory(directory: Path, *, recursive: bool) -> dict[tuple[Path, str], list[Path]]:
    groups: dict[tuple[Path, str], list[Path]] = {}
    for path in _iter_candidate_files(directory, recursive=recursive):
        groups.setdefault((path.parent.resolve(), path.stem), []).append(path.resolve())
    return groups


def discover_jobs(
    input_path: Path,
    *,
    name: str | None = None,
    recursive: bool = False,
) -> list[MediaJob]:
    """Discover same-stem lyrics/media groups.

    Accepted inputs:
    - an existing media or TXT file;
    - an existing directory;
    - a non-existing basename such as ``songs/foo`` (searched in its parent).

    A video is used as the visual source when present. A same-stem audio sidecar
    is preferred as the alignment/separation source. Every job requires exactly
    one discoverable ``<stem>.txt`` and at least one supported media file.
    """

    path = input_path.expanduser()
    selected_stem: str | None = name
    if path.exists() and path.is_file():
        directory = path.parent.resolve()
        selected_stem = selected_stem or path.stem
        groups = _group_directory(directory, recursive=False)
    elif path.exists() and path.is_dir():
        directory = path.resolve()
        groups = _group_directory(directory, recursive=recursive)
    else:
        directory = (path.parent if str(path.parent) not in ("", ".") else Path.cwd()).resolve()
        if not directory.is_dir():
            raise FileNotFoundError(directory)
        selected_stem = selected_stem or path.name
        groups = _group_directory(directory, recursive=False)

    jobs: list[MediaJob] = []
    for (parent, stem), files in sorted(groups.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        if selected_stem is not None and stem != selected_stem:
            continue
        lyrics = _pick(files, LYRICS_EXTENSIONS)
        video = _pick(files, VIDEO_EXTENSIONS)
        audio = _pick(files, AUDIO_EXTENSIONS)
        if lyrics is None or (video is None and audio is None):
            continue
        jobs.append(MediaJob(stem=stem, parent=parent, lyrics=lyrics, video=video, audio=audio))

    if not jobs:
        target = selected_stem or "all same-stem groups"
        raise FileNotFoundError(
            f"no complete lyrics/media group found for {target!r} under {directory}; "
            "expected <name>.txt plus a supported video or audio file"
        )
    return jobs


def discover_job_with_explicit_lyrics(media_path: Path, lyrics_path: Path) -> MediaJob:
    """Create one job from a media file plus a separately selected lyrics TXT."""

    media = media_path.expanduser().resolve()
    lyrics = lyrics_path.expanduser().resolve()
    if not media.is_file():
        raise FileNotFoundError(f"media input is not a file: {media}")
    if not lyrics.is_file():
        raise FileNotFoundError(f"lyrics input is not a file: {lyrics}")
    if lyrics.suffix.lower() not in LYRICS_EXTENSIONS:
        raise ValueError(f"lyrics file must be a TXT file: {lyrics}")

    suffix = media.suffix.lower()
    if suffix in VIDEO_EXTENSIONS:
        return MediaJob(stem=media.stem, parent=media.parent, lyrics=lyrics, video=media, audio=None)
    if suffix in AUDIO_EXTENSIONS:
        return MediaJob(stem=media.stem, parent=media.parent, lyrics=lyrics, video=None, audio=media)
    raise ValueError(
        f"unsupported media extension {media.suffix!r}; "
        f"choose video {VIDEO_EXTENSIONS} or audio {AUDIO_EXTENSIONS}"
    )


def parse_individual_mode(value: str) -> IndividualMode:
    parts = tuple(part.strip().lower() for part in value.split(":"))
    if len(parts) != 3:
        raise ValueError(f"individual mode must be MODEL:AUDIO:MODE, got {value!r}")
    model, audio, mode = parts
    if model not in MODELS:
        raise ValueError(f"unsupported model {model!r}; choose from {MODELS}")
    if audio not in AUDIO_INPUTS:
        raise ValueError(f"unsupported audio input {audio!r}; choose from {AUDIO_INPUTS}")
    if mode not in ALIGNMENT_MODES:
        raise ValueError(f"unsupported alignment mode {mode!r}; choose from {ALIGNMENT_MODES}")
    return IndividualMode(model=model, audio=audio, mode=mode)


def parse_compare_models(value: str) -> tuple[str, str]:
    parts = tuple(part.strip().lower() for part in value.split(":"))
    if len(parts) != 2:
        raise ValueError(f"compare-models must be AUDIO:MODE, got {value!r}")
    audio, mode = parts
    if audio not in AUDIO_INPUTS or mode not in ALIGNMENT_MODES:
        raise ValueError(f"unsupported compare-models selection {value!r}")
    return audio, mode


def _all_individuals() -> set[IndividualMode]:
    return {
        IndividualMode(model=model, audio=audio, mode=mode)
        for model in MODELS
        for audio in AUDIO_INPUTS
        for mode in ALIGNMENT_MODES
    }


def build_output_plan(
    *,
    presets: Sequence[str] = (),
    individuals: Sequence[str] = (),
    compare_models: Sequence[str] = (),
    compare_inputs: Sequence[str] = (),
) -> OutputPlan:
    selected_individuals: set[IndividualMode] = set()
    selected_compare_models: set[tuple[str, str]] = set()
    selected_compare_inputs: set[str] = set()

    if not presets and not individuals and not compare_models and not compare_inputs:
        presets = ("default",)

    for preset in presets:
        normalized = preset.strip().lower()
        if normalized == "default":
            selected_individuals.add(IndividualMode("r2", "vocal", "windowed"))
        elif normalized == "all-individual":
            selected_individuals.update(_all_individuals())
        elif normalized == "compare-models":
            selected_compare_models.add(("vocal", "windowed"))
        elif normalized == "compare-inputs":
            selected_compare_inputs.add("r2")
        elif normalized == "full-demo":
            selected_individuals.update(_all_individuals())
            selected_compare_models.update(
                (audio, mode) for audio in AUDIO_INPUTS for mode in ALIGNMENT_MODES
            )
            selected_compare_inputs.update(MODELS)
        else:
            raise ValueError(
                f"unsupported preset {preset!r}; choose default, all-individual, "
                "compare-models, compare-inputs, or full-demo"
            )

    selected_individuals.update(parse_individual_mode(value) for value in individuals)
    selected_compare_models.update(parse_compare_models(value) for value in compare_models)
    for model in compare_inputs:
        normalized = model.strip().lower()
        if normalized not in MODELS:
            raise ValueError(f"unsupported compare-inputs model {model!r}")
        selected_compare_inputs.add(normalized)

    for audio, mode in selected_compare_models:
        selected_individuals.update(
            IndividualMode(model=model, audio=audio, mode=mode) for model in MODELS
        )
    for model in selected_compare_inputs:
        selected_individuals.update(
            IndividualMode(model=model, audio=audio, mode=mode)
            for audio in AUDIO_INPUTS
            for mode in ALIGNMENT_MODES
        )

    return OutputPlan(
        individuals=tuple(sorted(selected_individuals)),
        compare_models=tuple(sorted(selected_compare_models)),
        compare_inputs=tuple(model for model in MODELS if model in selected_compare_inputs),
    )
