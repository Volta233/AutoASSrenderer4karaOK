#!/usr/bin/env python3
"""Batch known-lyrics alignment and karaoke rendering for same-stem media/TXT groups.

Default output is one R2 + separated-vocal + strict-windowed alignment/video per
job. Models are loaded once per selected model and reused across all jobs.
"""
from __future__ import annotations

import argparse
import gc
import hashlib
import importlib.util
import json
import os
import shutil
import shlex
import subprocess
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from lyricalign.demo.alignment_artifacts import write_alignment_bundle  # noqa: E402
from lyricalign.demo.batch import (  # noqa: E402
    AUDIO_INPUTS,
    ALIGNMENT_MODES,
    MODELS,
    IndividualMode,
    MediaJob,
    OutputPlan,
    build_output_plan,
    discover_job_with_explicit_lyrics,
    discover_jobs,
)
from lyricalign.demo.media_render import (  # noqa: E402
    SubtitleStyle,
    atomic_json,
    canonical_hash,
    detect_font,
    normalize_ass_color,
    render_composite,
    render_media_video,
    sha256,
)
from lyricalign.demo.spleeter_model import resolve_spleeter_model  # noqa: E402
from lyricalign.demo.karaoke import (  # noqa: E402
    alignment_unit_mode,
    normalize_alignment_language,
    parse_lyrics_text,
)
from lyricalign.training.qwen_fa_runtime import decode_audio  # noqa: E402
from scripts.demo.align_qwen_fa_serial_demo import (  # noqa: E402
    WINDOW_POLICY,
    checkpoint_identity,
    full_alignment,
    load_model,
    output_is_current,
    windowed_alignment,
)

SCHEMA_VERSION = "qwen_fa_batch_alignment_v4_forward_overlap_compression"
DEFAULT_MODEL_ID = (
    "/root/autodl-tmp/AST_storage/Data/lyricalign/models/hf_cache/"
    "models--Qwen--Qwen3-ForcedAligner-0.6B-hf/snapshots/"
    "c07281df297b9905d24a508279258cccf987a064"
)
DEFAULT_MODEL_REVISION = "c07281df297b9905d24a508279258cccf987a064"
DEFAULT_RUN_ROOT = Path("/root/autodl-tmp/AST_storage/Data/lyricalign/runs")
DEFAULT_R1_RUN = "20260723_qwen_fa_r1_projector_seed3407"
DEFAULT_R2_RUN = "20260723_qwen_fa_r2_full_seed3407"


def _log(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True), flush=True)


def _best_checkpoint(run_dir: Path) -> Path:
    best = run_dir / "best_checkpoint.json"
    if not best.is_file():
        raise FileNotFoundError(f"missing validation-selected checkpoint identity: {best}")
    payload = json.loads(best.read_text(encoding="utf-8"))
    for key in ("checkpoint", "checkpoint_path", "best_checkpoint"):
        value = payload.get(key)
        if value:
            path = Path(value)
            if not path.is_absolute():
                path = run_dir / path
            path = path.resolve()
            if not path.is_dir():
                raise FileNotFoundError(path)
            return path
    raise RuntimeError(f"best checkpoint JSON has no checkpoint path: {best}")


def _model_checkpoint(args: argparse.Namespace, model: str) -> tuple[str, Path | None]:
    if model == "r0":
        return "raw", None
    if model == "r1":
        path = args.r1_checkpoint or _best_checkpoint(args.r1_run)
        return "projector", path
    if model == "r2":
        path = args.r2_checkpoint or _best_checkpoint(args.r2_run)
        return "lora", path
    raise ValueError(model)




def _probe_duration(path: Path) -> float:
    command = [
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
    ]
    value = subprocess.run(command, check=True, text=True, capture_output=True).stdout.strip()
    duration = float(value)
    if duration <= 0:
        raise RuntimeError(f"non-positive media duration: {path}")
    return duration

def _write_mix_audio(source: Path, output: Path, identity: Path, *, force: bool) -> None:
    request = {
        "schema_version": "qwen_fa_batch_mix_v1",
        "source": str(source.resolve()),
        "source_sha256": sha256(source),
        "sample_rate": 44100,
        "channels": 2,
        "codec": "pcm_s16le",
    }
    request_hash = canonical_hash(request)
    if not force and output.is_file() and identity.is_file():
        try:
            if json.loads(identity.read_text(encoding="utf-8")).get("request_hash") == request_hash:
                return
        except (OSError, json.JSONDecodeError):
            pass
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp.wav")
    command = [
        "ffmpeg", "-nostdin", "-y", "-v", "warning", "-i", str(source), "-vn",
        "-ar", "44100", "-ac", "2", "-c:a", "pcm_s16le", str(temporary),
    ]
    _log({"prepare_mix": command})
    subprocess.run(command, check=True)
    temporary.replace(output)
    atomic_json(identity, {**request, "request_hash": request_hash, "output_sha256": sha256(output)})


def _ensure_mix_audio(source: Path, output: Path, identity: Path, *, force: bool) -> None:
    if force or not output.is_file() or not identity.is_file():
        _write_mix_audio(source, output, identity, force=True)
        return
    _write_mix_audio(source, output, identity, force=False)


def _spleeter_command(args: argparse.Namespace) -> list[str]:
    if args.spleeter_command:
        return args.spleeter_command.split()
    if shutil.which("spleeter"):
        return ["spleeter"]
    if shutil.which("conda"):
        return ["conda", "run", "-n", args.spleeter_env, "spleeter"]
    raise RuntimeError(
        "Spleeter is required for vocal mode. Install it in PATH or provide "
        "--spleeter-command/--spleeter-env."
    )


def _prepare_spleeter_vocals(
    *,
    mix: Path,
    mix_source: Path,
    work_audio: Path,
    args: argparse.Namespace,
    force: bool,
) -> tuple[Path, Path]:
    vocals = work_audio / "vocals.wav"
    accompaniment = work_audio / "accompaniment.wav"
    quality = work_audio / "separation_quality.json"
    identity = work_audio / "vocals.identity.json"
    model_info = resolve_spleeter_model(args.spleeter_model_root, args.spleeter_model_name)
    model_identity = model_info.as_dict()
    request = {
        "schema_version": "qwen_fa_batch_spleeter_v2_explicit_weights",
        "mix_sha256": sha256(mix),
        "model_root": str(model_info.model_root.resolve()),
        "model_dir": str(model_info.model_dir.resolve()),
        "model_name": args.spleeter_model_name,
        "model_identity_sha256": model_identity["identity_sha256"],
        "model_layout": model_identity["layout"],
        "model_marker_present": model_identity["marker_present"],
        "quality_policy": "reject_silent_or_near_copy_v1",
    }
    request_hash = canonical_hash(request)
    if not force and all(path.is_file() for path in (vocals, accompaniment, quality, identity)):
        try:
            identity_payload = json.loads(identity.read_text(encoding="utf-8"))
            quality_payload = json.loads(quality.read_text(encoding="utf-8"))
            if (
                identity_payload.get("request_hash") == request_hash
                and quality_payload.get("passed") is True
            ):
                return vocals, accompaniment
        except (OSError, json.JSONDecodeError):
            pass

    stage = _new_stage_dir(work_audio, "spleeter_stage")
    command = _spleeter_command(args) + [
        "separate", "-p", f"spleeter:{args.spleeter_model_name}", "-o", str(stage), str(mix),
    ]
    environment = os.environ.copy()
    environment["MODEL_PATH"] = str(model_info.model_root)
    _log({"spleeter": command, "MODEL_PATH": environment["MODEL_PATH"], "spleeter_model": model_identity})
    subprocess.run(command, check=True, env=environment)
    generated = stage / mix.stem
    generated_vocals = generated / "vocals.wav"
    generated_accompaniment = generated / "accompaniment.wav"
    if not generated_vocals.is_file() or not generated_accompaniment.is_file():
        raise FileNotFoundError(f"incomplete Spleeter output under {generated}")
    shutil.copy2(generated_vocals, vocals)
    shutil.copy2(generated_accompaniment, accompaniment)
    if not mix.is_file():
        _log({"warning": "mix_audio_missing_after_spleeter", "mix": str(mix), "action": "rebuild_mix_audio"})
        _write_mix_audio(mix_source, mix, work_audio / "mix.identity.json", force=True)

    check_command = [
        sys.executable, str(ROOT / "scripts" / "demo" / "check_audio_separation.py"),
        "--mix", str(mix), "--vocals", str(vocals),
        "--accompaniment", str(accompaniment), "--report", str(quality),
    ]
    _log({"separation_quality_check": check_command})
    subprocess.run(check_command, check=True)
    atomic_json(
        identity,
        {
            **request,
            "request_hash": request_hash,
            "vocals_sha256": sha256(vocals),
            "accompaniment_sha256": sha256(accompaniment),
            "quality_report_sha256": sha256(quality),
        },
    )
    return vocals, accompaniment


def _demucs_command(args: argparse.Namespace) -> list[str]:
    """Resolve Demucs without silently falling back to Spleeter.

    The active environment is preferred. Some installations expose the Python
    module but not a console-script on PATH, so ``python -m demucs`` is checked
    before the named conda environment fallback.
    """
    if args.demucs_command:
        return shlex.split(args.demucs_command)
    executable = shutil.which("demucs")
    if executable:
        return [executable]
    if importlib.util.find_spec("demucs") is not None:
        return [sys.executable, "-m", "demucs"]
    if shutil.which("conda") and _conda_env_exists(args.demucs_env):
        return ["conda", "run", "-n", args.demucs_env, "python", "-m", "demucs"]
    raise RuntimeError(
        "Demucs is required for --separator demucs. Install demucs==4.1.0 in the "
        "active environment, create the configured conda environment "
        f"{args.demucs_env!r}, or provide --demucs-command explicitly. "
        "Spleeter is not used as an automatic fallback."
    )


def _conda_env_exists(name: str) -> bool:
    if not name.strip() or not shutil.which("conda"):
        return False
    try:
        result = subprocess.run(
            ["conda", "env", "list", "--json"],
            check=True,
            text=True,
            capture_output=True,
        )
        payload = json.loads(result.stdout)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError):
        return False
    expected = name.casefold()
    for env in payload.get("envs", []):
        path = Path(str(env))
        if path.name.casefold() == expected or str(path).casefold() == expected:
            return True
    return False


def _new_stage_dir(work_audio: Path, prefix: str) -> Path:
    work_audio.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    for attempt in range(100):
        suffix = f"{stamp}_{os.getpid()}_{attempt}"
        stage = work_audio / f"{prefix}_{suffix}"
        try:
            stage.mkdir()
            return stage
        except FileExistsError:
            continue
    raise RuntimeError(f"could not create a unique temporary {prefix} directory under {work_audio}")


def _prepare_demucs_vocals(
    *,
    mix: Path,
    mix_source: Path,
    work_audio: Path,
    args: argparse.Namespace,
    force: bool,
) -> tuple[Path, Path]:
    vocals = work_audio / "vocals.wav"
    accompaniment = work_audio / "accompaniment.wav"
    quality = work_audio / "separation_quality.json"
    identity = work_audio / "vocals.identity.json"
    request = {
        "schema_version": "qwen_fa_batch_demucs_v1",
        "mix_sha256": sha256(mix),
        "separator": "demucs",
        "package_version_requested": args.demucs_version,
        "model_name": args.demucs_model,
        "device": args.demucs_device,
        "shifts": args.demucs_shifts,
        "overlap": args.demucs_overlap,
        "segment_sec": args.demucs_segment,
        "jobs": args.demucs_jobs,
        "clip_mode": args.demucs_clip_mode,
        "two_stems": "vocals",
        "other_method": "add",
        "torch_home": str(args.demucs_torch_home.resolve()) if args.demucs_torch_home else None,
        "quality_policy": "reject_silent_or_near_copy_v1",
    }
    request_hash = canonical_hash(request)
    if not force and all(path.is_file() for path in (vocals, accompaniment, quality, identity)):
        try:
            identity_payload = json.loads(identity.read_text(encoding="utf-8"))
            quality_payload = json.loads(quality.read_text(encoding="utf-8"))
            if identity_payload.get("request_hash") == request_hash and quality_payload.get("passed") is True:
                return vocals, accompaniment
        except (OSError, json.JSONDecodeError):
            pass
    if not force and vocals.is_file() and accompaniment.is_file():
        if not mix.is_file():
            _log({"warning": "mix_audio_missing_before_demucs_quality_resume", "mix": str(mix), "action": "rebuild_mix_audio"})
            _write_mix_audio(mix_source, mix, work_audio / "mix.identity.json", force=True)
        check_command = [
            sys.executable, str(ROOT / "scripts" / "demo" / "check_audio_separation.py"),
            "--mix", str(mix), "--vocals", str(vocals),
            "--accompaniment", str(accompaniment), "--report", str(quality),
        ]
        _log({"separation_quality_check": check_command, "resume_existing_stems": True})
        subprocess.run(check_command, check=True)
        atomic_json(
            identity,
            {
                **request,
                "request_hash": request_hash,
                "command": ["resume_existing_demucs_stems_after_quality_failure"],
                "vocals_sha256": sha256(vocals),
                "accompaniment_sha256": sha256(accompaniment),
                "quality_report_sha256": sha256(quality),
            },
        )
        return vocals, accompaniment

    command = _demucs_command(args)
    stage = _new_stage_dir(work_audio, "demucs_stage")
    command = command + [
        "-n", args.demucs_model,
        "--two-stems", "vocals",
        "--other-method", "add",
        "--out", str(stage),
        "--device", args.demucs_device,
        "--shifts", str(args.demucs_shifts),
        "--overlap", str(args.demucs_overlap),
        "--jobs", str(args.demucs_jobs),
        "--clip-mode", args.demucs_clip_mode,
    ]
    if args.demucs_segment is not None:
        command.extend(["--segment", str(args.demucs_segment)])
    command.append(str(mix))
    environment = os.environ.copy()
    if args.demucs_torch_home is not None:
        args.demucs_torch_home.mkdir(parents=True, exist_ok=True)
        environment["TORCH_HOME"] = str(args.demucs_torch_home)
    _log({"demucs": command, "TORCH_HOME": environment.get("TORCH_HOME"), "request": request})
    subprocess.run(command, check=True, env=environment)
    generated = stage / args.demucs_model / mix.stem
    generated_vocals = generated / "vocals.wav"
    generated_accompaniment = generated / "no_vocals.wav"
    if not generated_vocals.is_file() or not generated_accompaniment.is_file():
        raise FileNotFoundError(f"incomplete Demucs output under {generated}")
    shutil.copy2(generated_vocals, vocals)
    shutil.copy2(generated_accompaniment, accompaniment)
    if not mix.is_file():
        _log({"warning": "mix_audio_missing_after_demucs", "mix": str(mix), "action": "rebuild_mix_audio"})
        _write_mix_audio(mix_source, mix, work_audio / "mix.identity.json", force=True)

    check_command = [
        sys.executable, str(ROOT / "scripts" / "demo" / "check_audio_separation.py"),
        "--mix", str(mix), "--vocals", str(vocals),
        "--accompaniment", str(accompaniment), "--report", str(quality),
    ]
    _log({"separation_quality_check": check_command})
    subprocess.run(check_command, check=True)
    atomic_json(
        identity,
        {
            **request,
            "request_hash": request_hash,
            "command": command,
            "vocals_sha256": sha256(vocals),
            "accompaniment_sha256": sha256(accompaniment),
            "quality_report_sha256": sha256(quality),
        },
    )
    return vocals, accompaniment


def _prepare_vocals(
    *,
    mix: Path,
    mix_source: Path,
    work_audio: Path,
    args: argparse.Namespace,
    force: bool,
) -> tuple[Path, Path]:
    if args.separator == "spleeter":
        return _prepare_spleeter_vocals(
            mix=mix,
            mix_source=mix_source,
            work_audio=work_audio,
            args=args,
            force=force,
        )
    if args.separator == "demucs":
        return _prepare_demucs_vocals(
            mix=mix,
            mix_source=mix_source,
            work_audio=work_audio,
            args=args,
            force=force,
        )
    raise ValueError(f"unsupported separator: {args.separator}")


def _job_output_root(job: MediaJob, args: argparse.Namespace) -> Path:
    if args.output_dir is None:
        return job.parent / f"{job.stem}_qwen_fa"
    return args.output_dir.resolve() / job.stem


def _prepare_job(job: MediaJob, plan: OutputPlan, args: argparse.Namespace) -> dict[str, Path]:
    if job.video is not None and job.audio is not None:
        video_duration = _probe_duration(job.video)
        audio_duration = _probe_duration(job.audio)
        difference = abs(video_duration - audio_duration)
        if difference > args.media_duration_tolerance_sec and not args.allow_duration_mismatch:
            raise RuntimeError(
                "same-stem video/audio duration mismatch exceeds tolerance: "
                f"video={video_duration:.3f}s audio={audio_duration:.3f}s "
                f"difference={difference:.3f}s tolerance={args.media_duration_tolerance_sec:.3f}s"
            )
        if difference > args.media_duration_tolerance_sec:
            _log({
                "warning": "same_stem_duration_mismatch_allowed",
                "job": job.stem,
                "video_duration_sec": video_duration,
                "audio_duration_sec": audio_duration,
                "difference_sec": difference,
            })
    out_root = _job_output_root(job, args)
    work_audio = out_root / "work" / "audio"
    mix = work_audio / "mix.wav"
    _ensure_mix_audio(
        job.mix_source,
        mix,
        work_audio / "mix.identity.json",
        force=args.force_prepare,
    )
    paths = {"mix": mix}
    if "vocal" in plan.required_audio_inputs:
        vocals, accompaniment = _prepare_vocals(
            mix=mix,
            mix_source=job.mix_source,
            work_audio=work_audio,
            args=args,
            force=args.force_prepare or args.force_separation,
        )
        paths["vocal"] = vocals
        paths["accompaniment"] = accompaniment
    return paths


def _existing_render_audio_paths(
    job: MediaJob, plan: OutputPlan, args: argparse.Namespace
) -> dict[str, Path]:
    """Resolve render inputs without invoking separation or validating model weights."""
    out_root = _job_output_root(job, args)
    work_audio = out_root / "work" / "audio"
    cached_mix = work_audio / "mix.wav"
    paths: dict[str, Path] = {"mix": cached_mix if cached_mix.is_file() else job.mix_source}
    needs_aligned_vocal = (
        args.render_audio == "aligned"
        and any(spec.audio == "vocal" for spec in plan.individuals)
    )
    if needs_aligned_vocal:
        vocal = work_audio / "vocals.wav"
        if not vocal.is_file():
            raise FileNotFoundError(
                "render-only requested aligned vocal audio but cached vocals.wav is missing: "
                f"{vocal}. Run --stage prepare with the intended --separator first."
            )
        paths["vocal"] = vocal
    else:
        # Source-audio rendering never reads the separated stem.  Keep a value
        # for callers that inspect the mapping, but do not require it to exist.
        paths["vocal"] = work_audio / "vocals.wav"
    accompaniment = work_audio / "accompaniment.wav"
    if accompaniment.is_file():
        paths["accompaniment"] = accompaniment
    return paths


def _alignment_args(args: argparse.Namespace) -> SimpleNamespace:
    return SimpleNamespace(
        model=str(args.model),
        revision=str(args.revision),
        local_files_only=args.local_files_only,
        cache_dir=args.cache_dir,
        device=args.device,
        language=args.language,
        timestamp_segment_sec=args.timestamp_segment_sec,
        core_sec=args.core_sec,
        left_context_sec=args.left_context_sec,
        right_context_sec=args.right_context_sec,
        future_line_padding=args.future_line_padding,
        minimum_forward_characters=args.minimum_forward_characters,
        future_character_ratio=args.future_character_ratio,
        max_candidate_expansions=args.max_candidate_expansions,
        boundary_start_tolerance_sec=args.boundary_start_tolerance_sec,
        seam_tolerance_sec=args.seam_tolerance_sec,
    )


def _alignment_request(
    *,
    job: MediaJob,
    model_name: str,
    checkpoint_info: dict[str, Any],
    lyrics_info: dict[str, Any],
    audio_name: str,
    audio_path: Path,
    mode: str,
    args: argparse.Namespace,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "job": {
            "stem": job.stem,
            "lyrics": str(job.lyrics.resolve()),
            "video": str(job.video.resolve()) if job.video else None,
            "audio": str(job.audio.resolve()) if job.audio else None,
        },
        "model_name": model_name,
        "model_id": str(args.model),
        "revision": args.revision,
        "language": args.language,
        "alignment_unit_mode": alignment_unit_mode(args.language),
        "checkpoint": checkpoint_info,
        "lyrics": lyrics_info,
        "audio_name": audio_name,
        "audio": {"path": str(audio_path.resolve()), "sha256": sha256(audio_path)},
        "mode": mode,
        "timestamp_segment_sec": args.timestamp_segment_sec,
        "window": {
            "core_sec": args.core_sec,
            "left_context_sec": args.left_context_sec,
            "right_context_sec": args.right_context_sec,
            "policy": WINDOW_POLICY,
            "future_line_padding": args.future_line_padding,
            "minimum_forward_characters": args.minimum_forward_characters,
            "future_character_ratio": args.future_character_ratio,
            "max_candidate_expansions": args.max_candidate_expansions,
            "overlap_resolution": "forward_compress_to_previous_committed_end",
            "allows_zero_duration_after_compression": True,
            "legacy_boundary_start_tolerance_sec_ignored": args.boundary_start_tolerance_sec,
            "legacy_seam_tolerance_sec_diagnostic_only": args.seam_tolerance_sec,
        } if mode == "windowed" else None,
    }


def _write_alignment(
    *,
    job: MediaJob,
    out_root: Path,
    mode_spec: IndividualMode,
    processor: Any,
    model: Any,
    decoded_audio: Any,
    audio_path: Path,
    checkpoint_info: dict[str, Any],
    args: argparse.Namespace,
) -> Path:
    document = parse_lyrics_text(job.lyrics.read_text(encoding="utf-8-sig"), language=args.language)
    lyrics_info = {
        "path": str(job.lyrics.resolve()),
        "sha256": sha256(job.lyrics),
        "line_count": len(document.lines),
        "character_count": len(document.characters),
        "alignment_unit_count": len(document.characters),
        "language": document.language,
        "alignment_unit_mode": document.unit_mode,
    }
    structure = out_root / "lyrics_structure.json"
    # Cheap and deterministic; always rewrite so changing --language cannot
    # leave a stale character-level structure beside a new word-level result.
    atomic_json(
        structure,
        {
            "schema_version": SCHEMA_VERSION,
            "identity": lyrics_info,
            "lines": [line.__dict__ for line in document.lines],
            "characters": [item.__dict__ for item in document.characters],
        },
    )
    request = _alignment_request(
        job=job,
        model_name=mode_spec.model,
        checkpoint_info=checkpoint_info,
        lyrics_info=lyrics_info,
        audio_name=mode_spec.audio,
        audio_path=audio_path,
        mode=mode_spec.mode,
        args=args,
    )
    request_hash = canonical_hash(request)
    output = (
        out_root / "alignments" / mode_spec.model / mode_spec.audio / mode_spec.mode / "alignment.json"
    )
    progress_output = output.with_name("alignment.progress.json")
    failure_output = output.with_name("alignment.failure.json")
    if not args.force_align and output_is_current(output, request_hash):
        _log({"skip_alignment": str(output), "reason": "identity_match"})
        return output

    align_args = _alignment_args(args)
    # A failed rerun must never leave an older successful alignment beside a
    # new request identity.  Progress and failure artifacts preserve the exact
    # state even when no final alignment.json can be produced.
    output.unlink(missing_ok=True)
    failure_output.unlink(missing_ok=True)

    def write_progress(state: dict[str, Any]) -> None:
        atomic_json(
            progress_output,
            {
                "schema_version": "qwen_fa_alignment_progress_v1",
                "updated_at": datetime.now(timezone.utc).isoformat(),
                "identity": {**request, "request_hash": request_hash},
                "state": state,
            },
        )

    write_progress({"event": "alignment_started", "mode": mode_spec.mode})
    try:
        if mode_spec.mode == "full":
            rows, trace = full_alignment(processor, model, decoded_audio, document, align_args)
        else:
            rows, trace = windowed_alignment(
                processor,
                model,
                decoded_audio,
                document,
                align_args,
                progress_callback=write_progress,
            )
    except Exception as exc:  # noqa: BLE001
        latest_progress: dict[str, Any] | None = None
        try:
            latest_progress = json.loads(progress_output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            pass
        atomic_json(
            failure_output,
            {
                "schema_version": "qwen_fa_alignment_failure_v1",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "identity": {**request, "request_hash": request_hash},
                "error": {
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "traceback": traceback.format_exc(),
                    "diagnostic": getattr(exc, "diagnostic", None),
                },
                "latest_progress": latest_progress,
            },
        )
        raise
    repaired_count = sum(bool(row.get("cross_window_repaired")) for row in rows)
    seam_repaired_count = sum(bool(row.get("seam_repaired")) for row in rows)
    overlap_compressed = [row for row in rows if row.get("overlap_compressed")]
    overlap_collapsed = [
        row for row in overlap_compressed
        if row.get("overlap_compression_collapsed_to_zero")
    ]
    overlap_max_sec = max(
        (float(row.get("overlap_compression_sec", 0.0)) for row in overlap_compressed),
        default=0.0,
    )
    payload = {
        "schema_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "identity": {**request, "request_hash": request_hash},
        "summary": {
            "audio_duration_sec": float(len(decoded_audio) / 16000.0),
            "line_count": len(document.lines),
            "character_count": len(rows),
            "alignment_unit_count": len(rows),
            "language": document.language,
            "alignment_unit_mode": document.unit_mode,
            "cross_window_repaired_character_count": repaired_count,
            "cross_window_repaired_character_rate": repaired_count / len(rows),
            "seam_repaired_character_count": seam_repaired_count,
            "seam_repaired_character_rate": seam_repaired_count / len(rows),
            "overlap_compressed_character_count": len(overlap_compressed),
            "overlap_compressed_character_rate": len(overlap_compressed) / len(rows),
            "overlap_compression_collapsed_to_zero_count": len(overlap_collapsed),
            "overlap_compression_max_sec": overlap_max_sec,
            "window_policy": WINDOW_POLICY if mode_spec.mode == "windowed" else None,
            "window_count": len(trace) if mode_spec.mode == "windowed" else 1,
            "diagnostic_only": True,
            "uses_full_alignment_as_window_input": False,
        },
        "lines": [line.__dict__ for line in document.lines],
        "characters": rows,
        "window_trace": trace,
    }
    artifact_result = write_alignment_bundle(output, payload)
    progress_output.unlink(missing_ok=True)
    failure_output.unlink(missing_ok=True)
    _log({"completed_alignment": str(output), "quality_status": artifact_result["quality"]["status"], "artifacts": artifact_result["paths"]})
    return output


def _align_all(
    jobs: list[MediaJob],
    prepared: dict[str, dict[str, Path]],
    plan: OutputPlan,
    args: argparse.Namespace,
    failures: list[dict[str, Any]],
) -> None:
    specs_by_model: dict[str, list[IndividualMode]] = {
        model: [spec for spec in plan.individuals if spec.model == model]
        for model in plan.required_models
    }
    align_args = _alignment_args(args)
    for model_name in plan.required_models:
        kind, checkpoint = _model_checkpoint(args, model_name)
        checkpoint_info = checkpoint_identity(kind, checkpoint)
        pending = False
        for job in jobs:
            out_root = _job_output_root(job, args)
            document = parse_lyrics_text(
                job.lyrics.read_text(encoding="utf-8-sig"),
                language=args.language,
            )
            lyrics_info = {
                "path": str(job.lyrics.resolve()),
                "sha256": sha256(job.lyrics),
                "line_count": len(document.lines),
                "character_count": len(document.characters),
                "alignment_unit_count": len(document.characters),
                "language": document.language,
                "alignment_unit_mode": document.unit_mode,
            }
            for spec in specs_by_model[model_name]:
                request = _alignment_request(
                    job=job,
                    model_name=model_name,
                    checkpoint_info=checkpoint_info,
                    lyrics_info=lyrics_info,
                    audio_name=spec.audio,
                    audio_path=prepared[job.stem][spec.audio],
                    mode=spec.mode,
                    args=args,
                )
                output = out_root / "alignments" / model_name / spec.audio / spec.mode / "alignment.json"
                if args.force_align or not output_is_current(output, canonical_hash(request)):
                    pending = True
                    break
            if pending:
                break
        if not pending:
            _log({"skip_model": model_name, "reason": "all_selected_outputs_current"})
            continue

        _log({"loading_model": model_name, "checkpoint_kind": kind})
        processor, model = load_model(align_args, kind, checkpoint)
        try:
            for job in jobs:
                decoded: dict[str, Any] = {}
                for spec in specs_by_model[model_name]:
                    try:
                        if spec.audio not in decoded:
                            decoded[spec.audio] = decode_audio(prepared[job.stem][spec.audio])
                        _write_alignment(
                            job=job,
                            out_root=_job_output_root(job, args),
                            mode_spec=spec,
                            processor=processor,
                            model=model,
                            decoded_audio=decoded[spec.audio],
                            audio_path=prepared[job.stem][spec.audio],
                            checkpoint_info=checkpoint_info,
                            args=args,
                        )
                    except Exception as exc:  # noqa: BLE001
                        failure_output = (
                            _job_output_root(job, args)
                            / "alignments" / model_name / spec.audio / spec.mode
                            / "alignment.failure.json"
                        )
                        row = {
                            "stage": "align",
                            "job": job.stem,
                            "mode": spec.token,
                            "error": f"{type(exc).__name__}: {exc}",
                            "diagnostic": str(failure_output) if failure_output.is_file() else None,
                        }
                        failures.append(row)
                        _log({"failure": row})
                        if args.fail_fast:
                            raise
        finally:
            del model
            del processor
            gc.collect()
            try:
                import torch

                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except ImportError:
                pass


def _mode_label(spec: IndividualMode) -> str:
    audio = {"mix": "原音频对齐", "vocal": "分离人声对齐"}[spec.audio]
    mode = {"full": "整段", "windowed": "严格串行分窗"}[spec.mode]
    return f"{spec.model.upper()} · {audio} · {mode}"


def _render_job(
    *,
    job: MediaJob,
    prepared: dict[str, Path],
    plan: OutputPlan,
    args: argparse.Namespace,
    font: str,
) -> list[dict[str, Any]]:
    out_root = _job_output_root(job, args)
    individual_results: dict[IndividualMode, dict[str, Any]] = {}
    rows: list[dict[str, Any]] = []
    for spec in plan.individuals:
        alignment = out_root / "alignments" / spec.model / spec.audio / spec.mode / "alignment.json"
        if not alignment.is_file():
            failure = alignment.with_name("alignment.failure.json")
            rows.append(
                {
                    "kind": "individual",
                    "selection": spec.token,
                    "status": "skipped",
                    "reason": "alignment_failed_or_missing",
                    "alignment": str(alignment),
                    "diagnostic": str(failure) if failure.is_file() else None,
                }
            )
            continue
        audio_track = prepared["mix"] if args.render_audio == "source" else prepared[spec.audio]
        stem = f"{spec.model}_{spec.audio}_{spec.mode}"
        result = render_media_video(
            alignment_path=alignment,
            visual_source=job.video,
            audio_track=audio_track,
            output_path=out_root / "videos" / "individual" / f"{stem}.mp4",
            ass_path=out_root / "subtitles" / f"{stem}.ass",
            label=f"{job.stem} · {args.language} · {_mode_label(spec)}",
            font=font,
            force=args.force_render,
            subtitle_band_height=args.subtitle_band_height,
            audio_width=args.audio_width,
            audio_height=args.audio_height,
            background_image=args.background_image,
            subtitle_style=args.subtitle_style,
        )
        individual_results[spec] = result
        rows.append({"kind": "individual", "selection": spec.token, **result})

    for audio, mode in plan.compare_models:
        specs = [IndividualMode(model, audio, mode) for model in MODELS]
        missing = [spec.token for spec in specs if spec not in individual_results]
        if missing:
            rows.append(
                {
                    "kind": "compare_models",
                    "audio": audio,
                    "mode": mode,
                    "status": "skipped",
                    "reason": "missing_individual_render",
                    "missing": missing,
                }
            )
            continue
        result = render_composite(
            sources=[Path(individual_results[spec]["path"]) for spec in specs],
            source_hashes=[str(individual_results[spec]["request_hash"]) for spec in specs],
            output_path=out_root / "videos" / "comparisons" / f"compare_models_{audio}_{mode}.mp4",
            layout="three",
            force=args.force_render,
        )
        rows.append({"kind": "compare_models", "audio": audio, "mode": mode, **result})

    for model in plan.compare_inputs:
        specs = [
            IndividualMode(model, "mix", "full"),
            IndividualMode(model, "mix", "windowed"),
            IndividualMode(model, "vocal", "full"),
            IndividualMode(model, "vocal", "windowed"),
        ]
        missing = [spec.token for spec in specs if spec not in individual_results]
        if missing:
            rows.append(
                {
                    "kind": "compare_inputs",
                    "model": model,
                    "status": "skipped",
                    "reason": "missing_individual_render",
                    "missing": missing,
                }
            )
            continue
        result = render_composite(
            sources=[Path(individual_results[spec]["path"]) for spec in specs],
            source_hashes=[str(individual_results[spec]["request_hash"]) for spec in specs],
            output_path=out_root / "videos" / "comparisons" / f"compare_inputs_{model}.mp4",
            layout="four",
            force=args.force_render,
        )
        rows.append({"kind": "compare_inputs", "model": model, **result})
    return rows


def _render_failures(job: MediaJob, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for row in rows:
        if row.get("status") != "skipped":
            continue
        reason = row.get("reason")
        if reason == "alignment_failed_or_missing":
            failures.append(
                {
                    "stage": "render",
                    "job": job.stem,
                    "selection": row.get("selection"),
                    "error": f"missing required alignment for render: {row.get('alignment')}",
                    "diagnostic": row.get("diagnostic"),
                }
            )
    return failures


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Discover same-stem media/TXT groups, align lyrics, and render karaoke videos. "
            "Default: R2 + vocal + strict windowed."
        )
    )
    parser.add_argument("input", type=Path, help="media file, TXT file, basename, or directory")
    parser.add_argument("--lyrics", type=Path, help="explicit lyrics TXT for a single media input")
    parser.add_argument("--name", help="only process this exact stem when input is a directory")
    parser.add_argument("--recursive", action="store_true", help="scan directory recursively")
    parser.add_argument("--output-dir", type=Path, help="output root; default is <source>/<stem>_qwen_fa")
    parser.add_argument("--media-duration-tolerance-sec", type=float, default=0.5, help="maximum allowed same-stem video/audio duration difference")
    parser.add_argument("--allow-duration-mismatch", action="store_true", help="continue despite same-stem video/audio duration mismatch")
    parser.add_argument("--stage", choices=("all", "prepare", "align", "render"), default="all")
    parser.add_argument("--preset", action="append", default=[], help="default, all-individual, compare-models, compare-inputs, or full-demo")
    parser.add_argument("--individual", action="append", default=[], metavar="MODEL:AUDIO:MODE")
    parser.add_argument("--compare-models", action="append", default=[], metavar="AUDIO:MODE")
    parser.add_argument("--compare-inputs", action="append", default=[], metavar="MODEL")
    parser.add_argument("--render-audio", choices=("source", "aligned"), default="source", help="source keeps normal program audio; aligned uses mix/vocal diagnostic audio")
    parser.add_argument("--font", help="subtitle font; default is selected from --language")
    parser.add_argument("--subtitle-band-height", type=int)
    parser.add_argument("--background-image", type=Path, help="image background for audio-only outputs")
    parser.add_argument("--karaoke-primary-color", default="#FFE800", help="sung highlight color; #RRGGBB or ASS &HAABBGGRR")
    parser.add_argument("--karaoke-secondary-color", default="#909090", help="unsung karaoke color")
    parser.add_argument("--subtitle-preview-color", default="#A0A0A0", help="preview line color")
    parser.add_argument("--subtitle-outline-color", default="#101010", help="subtitle outline color")
    parser.add_argument("--audio-width", type=int, default=1280)
    parser.add_argument("--audio-height", type=int, default=720)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--fail-fast", action="store_true")

    parser.add_argument("--model", default=os.environ.get("MODEL_ID", DEFAULT_MODEL_ID))
    parser.add_argument("--revision", default=os.environ.get("MODEL_REVISION", DEFAULT_MODEL_REVISION))
    parser.add_argument("--run-root", type=Path, default=Path(os.environ.get("RUN_ROOT", DEFAULT_RUN_ROOT)))
    parser.add_argument("--r1-run", type=Path)
    parser.add_argument("--r2-run", type=Path)
    parser.add_argument("--r1-checkpoint", type=Path)
    parser.add_argument("--r2-checkpoint", type=Path)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--local-files-only", action="store_true", default=os.environ.get("HF_HUB_OFFLINE") == "1")
    parser.add_argument("--device", default="cuda")
    parser.add_argument(
        "--language",
        type=normalize_alignment_language,
        default="Chinese",
        metavar="LANGUAGE",
        help=(
            "Forced Aligner language (Chinese, English, Japanese, Cantonese, etc.; "
            "common aliases such as en/ja/zh/yue are accepted)"
        ),
    )
    parser.add_argument("--timestamp-segment-sec", type=float, default=0.08)
    parser.add_argument("--core-sec", type=float, default=60.0)
    parser.add_argument("--left-context-sec", type=float, default=10.0)
    parser.add_argument("--right-context-sec", type=float, default=10.0)
    parser.add_argument("--future-line-padding", type=int, default=1)
    parser.add_argument("--minimum-forward-characters", type=int, default=64)
    parser.add_argument("--future-character-ratio", type=float, default=1.35)
    parser.add_argument("--max-candidate-expansions", type=int, default=4)
    parser.add_argument(
        "--boundary-start-tolerance-sec",
        type=float,
        default=0.32,
        help="legacy compatibility only; v6 does not reject pre-core predictions",
    )
    parser.add_argument(
        "--seam-tolerance-sec",
        type=float,
        default=0.16,
        help="diagnostic legacy threshold only; v6 never limits overlap compression",
    )

    parser.add_argument("--separator", choices=("spleeter", "demucs"), default=os.environ.get("LYRICALIGN_SEPARATOR", "demucs"))
    parser.add_argument("--spleeter-model-root", type=Path, default=Path(os.environ.get("SPLEETER_MODEL_ROOT", Path.home() / ".cache/spleeter_models")))
    parser.add_argument("--spleeter-model-name", default="2stems")
    parser.add_argument("--spleeter-env", default=os.environ.get("SPLEETER_ENV", "spleeter"))
    parser.add_argument("--spleeter-command", help="explicit command prefix, e.g. 'conda run -n spleeter spleeter'")
    parser.add_argument("--demucs-version", default=os.environ.get("DEMUCS_VERSION", "4.1.0"))
    parser.add_argument("--demucs-model", default=os.environ.get("DEMUCS_MODEL", "htdemucs_ft"))
    parser.add_argument("--demucs-env", default=os.environ.get("DEMUCS_ENV", "demucs"))
    parser.add_argument("--demucs-command", help="explicit command prefix, e.g. 'conda run -n demucs demucs'")
    parser.add_argument("--demucs-device", default=os.environ.get("DEMUCS_DEVICE", "cuda"))
    parser.add_argument("--demucs-shifts", type=int, default=int(os.environ.get("DEMUCS_SHIFTS", "0")))
    parser.add_argument("--demucs-overlap", type=float, default=float(os.environ.get("DEMUCS_OVERLAP", "0.25")))
    parser.add_argument("--demucs-segment", type=int)
    parser.add_argument("--demucs-jobs", type=int, default=int(os.environ.get("DEMUCS_JOBS", "0")))
    parser.add_argument("--demucs-clip-mode", choices=("rescale", "clamp"), default="rescale")
    parser.add_argument("--demucs-torch-home", type=Path, default=Path(os.environ["TORCH_HOME"]) if os.environ.get("TORCH_HOME") else None)
    parser.add_argument("--force-prepare", action="store_true")
    parser.add_argument("--force-separation", action="store_true")
    parser.add_argument("--force-align", action="store_true")
    parser.add_argument("--force-render", action="store_true")
    parser.add_argument("--force", action="store_true", help="force prepare, separation, align, and render")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.r1_run = (args.r1_run or args.run_root / DEFAULT_R1_RUN).resolve()
    args.r2_run = (args.r2_run or args.run_root / DEFAULT_R2_RUN).resolve()
    args.spleeter_model_root = args.spleeter_model_root.expanduser().resolve()
    if args.demucs_torch_home is not None:
        args.demucs_torch_home = args.demucs_torch_home.expanduser().resolve()
    if args.force:
        args.force_prepare = True
        args.force_separation = True
        args.force_align = True
        args.force_render = True
    args.subtitle_style = SubtitleStyle(
        karaoke_primary=normalize_ass_color(args.karaoke_primary_color),
        karaoke_secondary=normalize_ass_color(args.karaoke_secondary_color),
        preview=normalize_ass_color(args.subtitle_preview_color),
        outline=normalize_ass_color(args.subtitle_outline_color),
    )

    plan = build_output_plan(
        presets=args.preset,
        individuals=args.individual,
        compare_models=args.compare_models,
        compare_inputs=args.compare_inputs,
    )
    if args.lyrics is not None:
        if args.name is not None or args.recursive:
            raise RuntimeError("--lyrics is only supported for a single media input; do not combine it with --name/--recursive")
        jobs = [discover_job_with_explicit_lyrics(args.input, args.lyrics)]
    else:
        jobs = discover_jobs(args.input, name=args.name, recursive=args.recursive)
    stems = [job.stem for job in jobs]
    if len(stems) != len(set(stems)) and args.output_dir is not None:
        raise RuntimeError("duplicate stems under a shared --output-dir; use non-recursive input or unique names")

    plan_payload = {
        "schema_version": "qwen_fa_batch_plan_v2_multilingual_units",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "input": str(args.input),
        "stage": args.stage,
        "jobs": [
            {
                "stem": job.stem,
                "lyrics": str(job.lyrics),
                "video": str(job.video) if job.video else None,
                "audio": str(job.audio) if job.audio else None,
                "mix_source": str(job.mix_source),
                "output_root": str(_job_output_root(job, args)),
            }
            for job in jobs
        ],
        "individuals": [item.token for item in plan.individuals],
        "compare_models": [f"{audio}:{mode}" for audio, mode in plan.compare_models],
        "compare_inputs": list(plan.compare_inputs),
        "language": args.language,
        "alignment_unit_mode": alignment_unit_mode(args.language),
        "render_audio": args.render_audio,
        "separator": args.separator,
        "separator_model": args.spleeter_model_name if args.separator == "spleeter" else args.demucs_model,
    }
    _log({"plan": plan_payload})
    if args.language != "Chinese" and any(item.model == "r2" for item in plan.individuals):
        _log({
            "warning": "r2_multilingual_not_validated",
            "language": args.language,
            "message": (
                "R2 was fine-tuned on Chinese singing data. The base R0 model officially supports "
                "this language, but R2 cross-language quality has not yet been validated."
            ),
        })
    if args.dry_run:
        return 0

    # Validate language-dependent unitization before media extraction or
    # Spleeter work.  Japanese therefore fails immediately when Nagisa is
    # missing instead of wasting separation/model time first.
    for job in jobs:
        document = parse_lyrics_text(
            job.lyrics.read_text(encoding="utf-8-sig"),
            language=args.language,
        )
        _log({
            "lyrics_preflight": job.stem,
            "language": document.language,
            "alignment_unit_mode": document.unit_mode,
            "alignment_unit_count": len(document.characters),
        })

    for command in ("ffmpeg", "ffprobe"):
        if not shutil.which(command):
            raise RuntimeError(f"required command not found: {command}")

    failures: list[dict[str, Any]] = []
    prepared: dict[str, dict[str, Path]] = {}
    for job in jobs:
        try:
            if args.stage == "render":
                prepared[job.stem] = _existing_render_audio_paths(job, plan, args)
            else:
                prepared[job.stem] = _prepare_job(job, plan, args)
            out_root = _job_output_root(job, args)
            atomic_json(out_root / "batch_plan.json", {**plan_payload, "job": job.stem})
        except Exception as exc:  # noqa: BLE001
            stage_name = "render_input_resolution" if args.stage == "render" else "prepare"
            row = {"stage": stage_name, "job": job.stem, "error": f"{type(exc).__name__}: {exc}"}
            failures.append(row)
            _log({"failure": row})
            if args.fail_fast:
                raise

    ready_jobs = [job for job in jobs if job.stem in prepared]
    if args.stage in ("all", "align"):
        _align_all(ready_jobs, prepared, plan, args, failures)

    render_rows: dict[str, list[dict[str, Any]]] = {}
    if args.stage in ("all", "render"):
        preferred_font = args.font or {
            "Japanese": "Noto Sans CJK JP",
            "Chinese": "Noto Sans CJK SC",
            "Cantonese": "Noto Sans CJK SC",
        }.get(args.language, "Noto Sans")
        font = detect_font(preferred_font)
        for job in ready_jobs:
            try:
                render_rows[job.stem] = _render_job(
                    job=job,
                    prepared=prepared[job.stem],
                    plan=plan,
                    args=args,
                    font=font,
                )
                for row in _render_failures(job, render_rows[job.stem]):
                    failures.append(row)
                    _log({"failure": row})
            except Exception as exc:  # noqa: BLE001
                row = {"stage": "render", "job": job.stem, "error": f"{type(exc).__name__}: {exc}"}
                failures.append(row)
                _log({"failure": row})
                if args.fail_fast:
                    raise

    for job in ready_jobs:
        out_root = _job_output_root(job, args)
        atomic_json(
            out_root / "batch_manifest.json",
            {
                "schema_version": "qwen_fa_batch_manifest_v2_multilingual_units",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "job": job.stem,
                "source": {
                    "lyrics": str(job.lyrics),
                    "video": str(job.video) if job.video else None,
                    "audio": str(job.audio) if job.audio else None,
                },
                "plan": plan_payload,
                "render_outputs": render_rows.get(job.stem, []),
                "failures": [row for row in failures if row.get("job") == job.stem],
                "status": "failed" if any(row.get("job") == job.stem for row in failures) else "complete",
            },
        )

    _log({"completed_jobs": len(ready_jobs), "failures": failures})
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
