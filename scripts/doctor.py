"""Check resources, imports, dependency consistency and devices without loading models."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

from python_support import SUPPORTED_PYTHON
from download_models import digest as file_sha256

ROOT = Path(__file__).resolve().parents[1]
ENV_KEYS = {
    "KTV_CPU_THREADS",
    "TIFA_SOURCE",
    "TIFA_MODEL",
    "TIFA_PYTHON",
    "TIFA_G2P",
    "KTV_ASR_MODEL",
    "KTV_LOCATOR_PYTHON",
    "KTV_KIM_SOURCE",
    "KTV_KIM_MODEL",
    "KTV_SEPARATION_PYTHON",
    "KTV_FFMPEG",
    "KTV_FFPROBE",
}
KIM_HASH = "87201f4d31afb5bc79993230fc49446918425574db48c01c405e44f365c7559e"


def configuration(root):
    values = {}
    # Parse only non-secret keys; API credentials are never collected or returned.
    candidates = (root / ".env", root / "ktv_app/.env", root / ".env.example")
    selected = next((p for p in candidates if p.is_file()), None)
    if selected:
        for line in selected.read_text(encoding="utf-8-sig").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key in ENV_KEYS:
                values[key] = value.strip().strip("\"'")
    values.update({key: os.environ[key] for key in ENV_KEYS if key in os.environ})
    return values


def check_resources(root, values, verify_weights=False, *, check_local_paths=True):
    records = []

    def resolve(key, fallback):
        value = Path(values.get(key) or fallback).expanduser()
        return (root / value).resolve() if not value.is_absolute() else value.resolve()

    def record(name, passed, details, **more):
        records.append(
            dict(
                name=name,
                status="passed" if passed else "failed",
                details=details,
                **more,
            )
        )

    def files(name, paths):
        missing = [str(p) for p in paths if not p.is_file() or p.stat().st_size == 0]
        record(
            name,
            not missing,
            "Resources present" if not missing else "Missing or empty resources",
            missing=missing,
        )
        return not missing

    tifa_source = resolve("TIFA_SOURCE", "models/forced_alignment/sources/TIFA")
    tifa_model = resolve(
        "TIFA_MODEL", "models/forced_alignment/tifa/TIFA-1.0-ST/model.pt"
    )
    model_dir = tifa_model.parent
    unidic = root / "models/forced_alignment/tifa/resources/UniDic/unidic"
    files(
        "TIFA source",
        [
            tifa_source / "requirements.txt",
            tifa_source / "lib/g2p_encoding.py",
            tifa_source / "LICENSE",
        ],
    )
    files(
        "TIFA model",
        [tifa_model, model_dir / "config.yaml", model_dir / "vocabulary.json"],
    )
    files(
        "TIFA pronunciation",
        [
            model_dir / "dictionaries" / name
            for name in (
                "ds-zh-pinyin-lite.txt",
                "japanese_dict_full.txt",
                "jyutping_dict.txt",
                "ds_cmudict-07b.txt",
            )
        ],
    )
    files(
        "TIFA English G2P",
        [
            model_dir / "assets/LstmG2p-Eng" / name
            for name in (
                "config.json",
                "char.json",
                "phonemes.json",
                "encoder.onnx",
                "decoder.onnx",
            )
        ],
    )
    files(
        "Japanese UniDic",
        [
            unidic / "mecabrc",
            unidic / "sys.dic",
            unidic / "unk.dic",
            unidic / "char.bin",
        ],
    )
    if check_local_paths:
        local_g2p = resolve("TIFA_G2P", str(model_dir / "g2p.local.yaml"))
        if files("Local pronunciation paths", [local_g2p]):
            try:
                import yaml

                content = yaml.safe_load(local_g2p.read_text(encoding="utf-8"))
                checked = []
                for converter in content.get("converters", []):
                    for key in ("dict_path", "model_path", "unidic_dir"):
                        path = converter.get("kwargs", {}).get(key)
                        if path:
                            checked.append(Path(path))
                missing = [str(p) for p in checked if not p.exists()]
                record(
                    "Resolved pronunciation paths",
                    bool(checked) and not missing,
                    "Regenerate with scripts/configure_g2p.py after moving the project"
                    if missing
                    else "Local paths resolve",
                    missing=missing,
                )
            except Exception as error:
                record("Resolved pronunciation paths", False, type(error).__name__)
    qwen = resolve("KTV_ASR_MODEL", "models/Qwen3-ASR-1.7B")
    qwen_config_ok = files(
        "Qwen ASR metadata",
        [
            qwen / name
            for name in (
                "config.json",
                "tokenizer_config.json",
                "preprocessor_config.json",
                "model.safetensors.index.json",
                "LICENSE",
            )
        ],
    )
    if qwen_config_ok:
        try:
            config = json.loads((qwen / "config.json").read_text(encoding="utf-8"))
            index = json.loads(
                (qwen / "model.safetensors.index.json").read_text(encoding="utf-8")
            )
            shards = sorted(set(index["weight_map"].values()))
            # The frozen 1.7B snapshot uses two shards and an approximately 4.7 GB checkpoint.
            missing = [
                name
                for name in shards
                if not (qwen / name).is_file() or (qwen / name).stat().st_size == 0
            ]
            size = sum(
                (qwen / name).stat().st_size
                for name in shards
                if (qwen / name).is_file()
            )
            valid = (
                bool(shards)
                and not missing
                and "asr" in str(config.get("model_type", "")).lower()
                and size > 3_000_000_000
            )
            record(
                "Qwen3-ASR-1.7B weights",
                valid,
                "Complete local ASR snapshot required; no forced aligner used",
                shards=len(shards),
                bytes=size,
                missing=missing,
            )
        except Exception as error:
            record("Qwen3-ASR-1.7B weights", False, type(error).__name__)
    kim_source = resolve("KTV_KIM_SOURCE", "models/separation/sources/Kim")
    kim_model = resolve(
        "KTV_KIM_MODEL",
        "models/separation/Kim/MelBandRoformer.ckpt",
    )
    files(
        "Kim source",
        [kim_source / "utils.py", kim_source / "models/mel_band_roformer/__init__.py"],
    )
    if (
        files(
            "Kim large checkpoint",
            [kim_model, kim_source / "configs/config_vocals_mel_band_roformer.yaml"],
        )
        and verify_weights
    ):
        digest = file_sha256(kim_model)
        record(
            "Kim checkpoint hash",
            digest == KIM_HASH,
            "SHA256 compared with the frozen large checkpoint",
            sha256=digest,
        )

    for name in ("ffmpeg", "ffprobe"):
        configured = values.get("KTV_" + name.upper())
        tool = (
            resolve("KTV_" + name.upper(), configured)
            if configured
            else shutil.which(name)
        )
        if not tool:
            tool = next((root / "tools").glob(f"ffmpeg/**/{name}.exe"), None)
        if not tool or not Path(tool).is_file():
            record(name, False, "Executable missing; configure KTV_" + name.upper())
            continue
        try:
            result = subprocess.run(
                [str(tool), "-version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=20,
            )
            record(
                name,
                result.returncode == 0,
                result.stdout.splitlines()[0]
                if result.stdout
                else "Cannot run executable",
            )
            if name == "ffmpeg":
                filters = subprocess.run(
                    [str(tool), "-hide_banner", "-filters"],
                    capture_output=True,
                    text=True,
                    timeout=20,
                )
                has_subtitles = (
                    filters.returncode == 0
                    and " subtitles " in filters.stdout
                    and " ass " in filters.stdout
                )
                record(
                    "FFmpeg subtitle filters",
                    has_subtitles,
                    "libass subtitles and ass filters required",
                )
        except (OSError, subprocess.TimeoutExpired) as error:
            record(name, False, type(error).__name__)
    return records


PROBE = r"""
import importlib, importlib.metadata, json, sys
modules = json.loads(sys.argv[1])
device = sys.argv[2]
result = {"imports": {}, "python": "%s.%s" % sys.version_info[:2]}
for name in modules:
    try:
        if name in {"qwen_asr", "nagisa"}:
            from ktv_app.native_paths import prepare_nagisa
            prepare_nagisa()
        module = importlib.import_module(name)
        result["imports"][name] = {"status": "passed", "version": str(getattr(module, "__version__", ""))}
    except Exception as error:
        result["imports"][name] = {"status": "failed", "error_type": type(error).__name__, "message": str(error)[:600]}
if "torch" in modules and result["imports"]["torch"]["status"] == "passed":
    import torch
    available = torch.cuda.is_available()
    selected = "cuda"
    result["device"] = {"requested": device, "selected": selected, "cuda_available": available, "torch_cuda_build": torch.version.cuda}
    try:
        if not available:
            raise RuntimeError("CUDA requested but PyTorch cannot use an NVIDIA GPU")
        data = torch.ones((2, 2), device=selected)
        result["device"]["small_tensor_check"] = bool(torch.isfinite(data @ data).all().item())
        if selected == "cuda":
            result["device"]["name"] = torch.cuda.get_device_name(0)
    except Exception as error:
        result["device"]["small_tensor_check"] = False
        result["device"]["error"] = str(error)[:600]
print("AIKTV_DOCTOR_JSON=" + json.dumps(result, ensure_ascii=False))
"""


def check_environments(root, values, device):
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    groups = {
        "app": (
            None,
            [
                "fastapi",
                "uvicorn",
                "httpx",
                "pydantic",
                "multipart",
                "dotenv",
                "numpy",
                "soundfile",
                "yaml",
                "textgrid",
                "ddgs",
                "opencc",
            ],
        ),
        "locator": (
            "KTV_LOCATOR_PYTHON",
            [
                "torch",
                "torchaudio",
                "qwen_asr",
                "transformers",
                "accelerate",
                "soundfile",
                "nagisa",
                "soynlp",
            ],
        ),
        "tifa": (
            "TIFA_PYTHON",
            [
                "torch",
                "torchaudio",
                "g2pflow",
                "lightning",
                "librosa",
                "pydantic",
                "yaml",
                "textgrid",
            ],
        ),
        "separation": (
            "KTV_SEPARATION_PYTHON",
            [
                "torch",
                "torchaudio",
                "ml_collections",
                "beartype",
                "rotary_embedding_torch",
                "einops",
                "librosa",
                "soundfile",
            ],
        ),
    }
    records = []
    for name, (key, modules) in groups.items():
        path = Path(values.get(key, "") or root / f".runtime/{name}/{suffix}")
        if not path.is_absolute():
            path = root / path
        record = dict(name=name + " environment", python=str(path), status="failed")
        if not path.is_file():
            record["details"] = "Environment missing; run install.cmd"
            records.append(record)
            continue
        try:
            process = subprocess.run(
                [str(path), "-c", PROBE, json.dumps(modules), device],
                cwd=root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=360,
            )
            payload = next(
                (
                    line.partition("=")[2]
                    for line in process.stdout.splitlines()
                    if line.startswith("AIKTV_DOCTOR_JSON=")
                ),
                None,
            )
            probe = json.loads(payload) if payload else {}
            pip = subprocess.run(
                [str(path), "-m", "pip", "check"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            imports_ok = bool(probe.get("imports")) and all(
                value["status"] == "passed" for value in probe["imports"].values()
            )
            device_ok = "device" not in probe or probe["device"].get(
                "small_tensor_check", False
            )
            passed = (
                process.returncode == 0
                and probe.get("python") in SUPPORTED_PYTHON
                and imports_ok
                and device_ok
                and pip.returncode == 0
            )
            record.update(
                status="passed" if passed else "failed",
                checks=probe,
                pip_check=dict(
                    status="passed" if pip.returncode == 0 else "failed",
                    output=(pip.stdout + pip.stderr).strip()[:2000],
                ),
            )
            if not payload:
                record["details"] = "Dependency probe could not complete"
                record["diagnostics"] = (process.stdout + process.stderr)[-2000:]
        except (OSError, subprocess.TimeoutExpired) as error:
            record["details"] = type(error).__name__
        records.append(record)
    return records


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--resources-only",
        action="store_true",
        help="Check assets/tools only; does not establish runnable dependencies",
    )
    parser.add_argument(
        "--verify-weights", action="store_true", help="Also hash the Kim checkpoint"
    )
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    values = configuration(root)
    device = "cuda"
    started = time.monotonic()
    records = check_resources(root, values, args.verify_weights)
    if not args.resources_only:
        records.extend(check_environments(root, values, device))
    passed = all(record["status"] == "passed" for record in records)
    result = dict(
        status="passed" if passed else "failed",
        project="LyriFrame",
        version=tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
            "project"
        ]["version"],
        device=device,
        checks=records,
        resources_only=args.resources_only,
        models_loaded=False,
        elapsed_seconds=round(time.monotonic() - started, 2),
    )
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    for record in records:
        print(f"[{record['status']}] {record['name']}: {record.get('details', '')}")
    print(
        json.dumps(
            {key: value for key, value in result.items() if key != "checks"},
            ensure_ascii=False,
        )
    )
    if not passed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
