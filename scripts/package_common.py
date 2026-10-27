"""Deterministic file manifests and streaming integrity verification for releases."""

from datetime import datetime, timezone
import json
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib
import zipfile

from python_support import PYTHON_LABEL
from download_models import digest, stream_digest

ROOT = Path(__file__).resolve().parents[1]
PREFIX = "LyriFrame-Kim/"
MANIFEST = "RELEASE_MANIFEST.json"
IGNORED = {
    ".git",
    ".runtime",
    ".venv",
    "venv",
    ".cache",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "test_runs",
    "outputs",
    "ktv_projects",
    "data",
    "dist",
    "build",
}


def build_package(entries, output, kind, extra=None):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    for name in entries:
        parts = Path(name).parts
        if (
            Path(name).is_absolute()
            or ".." in parts
            or any(part in IGNORED for part in parts)
        ):
            raise ValueError("Private or unsafe package path: " + name)
        if (
            Path(name).name.startswith(".env") and Path(name).name != ".env.example"
        ) or "ForcedAligner" in name:
            raise ValueError("Removed or private package asset: " + name)
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))[
        "project"
    ]
    temporary = output.with_suffix(output.suffix + ".tmp")
    files = {}
    print(
        json.dumps(
            dict(
                phase="packing",
                kind=kind,
                files=len(entries),
                uncompressed_bytes=sum(
                    Path(p).stat().st_size for p in entries.values()
                ),
            )
        ),
        flush=True,
    )
    with zipfile.ZipFile(
        temporary,
        "w",
        zipfile.ZIP_DEFLATED,
        compresslevel=9,
        allowZip64=True,
    ) as package:
        for name, path in sorted(entries.items()):
            files[name] = dict(bytes=Path(path).stat().st_size, sha256=digest(path))
            package.write(path, PREFIX + name)
        manifest = dict(
            schema_version=1,
            project="LyriFrame-Kim",
            version=project["version"],
            kind=kind,
            created_at=datetime.now(timezone.utc).isoformat(),
            python=PYTHON_LABEL,
            default_device="cuda",
            cuda_required=True,
            torch="2.11.0",
            torchaudio="2.11.0",
            credentials_included=False,
            environments_included=False,
            user_tasks_included=False,
            files=files,
            **(extra or {}),
        )
        package.writestr(
            PREFIX + MANIFEST, json.dumps(manifest, ensure_ascii=False, indent=2)
        )
    verify_zip(temporary)
    temporary.replace(output)
    result = dict(
        status="passed",
        project="LyriFrame-Kim",
        version=project["version"],
        kind=kind,
        files=len(files),
        bytes=output.stat().st_size,
        sha256=digest(output),
        output=str(output),
        all_file_hashes_verified=True,
        credentials_included=False,
        runtime_included=False,
        qwen_fa_included=False,
        models_included=False,
    )
    output.with_suffix(output.suffix + ".sha256").write_text(
        f"{result['sha256']}  {output.name}\n", encoding="utf-8"
    )
    output.with_suffix(output.suffix + ".audit.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return result


def archive_prefix(package):
    if PREFIX + MANIFEST not in package.namelist():
        raise ValueError("Expected a LyriFrame-Kim release manifest")
    return PREFIX


def verify_zip(path):
    with zipfile.ZipFile(path) as package:
        prefix = archive_prefix(package)
        manifest = json.loads(package.read(prefix + MANIFEST))
        expected = {prefix + name for name in manifest["files"]} | {prefix + MANIFEST}
        if set(package.namelist()) != expected or len(package.namelist()) != len(
            expected
        ):
            raise ValueError("Package file inventory differs from the release manifest")
        for name, record in manifest["files"].items():
            member = prefix + name
            if package.getinfo(member).file_size != record["bytes"]:
                raise ValueError("Package file size mismatch: " + name)
            with package.open(member) as stream:
                actual = stream_digest(stream)
            if actual != record["sha256"]:
                raise ValueError("Package hash mismatch: " + name)
        return dict(
            status="passed",
            project=manifest["project"],
            version=manifest["version"],
            kind=manifest["kind"],
            files=len(manifest["files"]),
            all_file_hashes_verified=True,
        )
