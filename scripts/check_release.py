"""Audit tracked/public source paths without displaying possible secret values."""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
IGNORED = {
    ".git",
    ".runtime",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    ".cache",
    "build",
    "venv",
}
PRIVATE = {"models", "tools", "ktv_projects", "data", "outputs", "dist"}
SECRET = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{24,}|-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----)"
)


def generated_download(relative):
    """Release downloads and interrupted restore scratch are not public source."""
    return (
        any(part.startswith("aiktv-restore-") for part in relative.parts)
        or relative.name.endswith((".zip", ".zip.parts.json"))
        or re.search(r"\.zip\.\d{3}$", relative.name) is not None
    )


def public_files():
    if (ROOT / ".git").exists():
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"],
            cwd=ROOT,
            capture_output=True,
            check=True,
        )
        return [ROOT / p for p in result.stdout.decode("utf-8").split("\0") if p]
    paths = []
    for directory, folders, files in os.walk(ROOT):
        folders[:] = [name for name in folders if name not in IGNORED | PRIVATE]
        for name in files:
            p = Path(directory) / name
            if (
                name.startswith(".env") and name != ".env.example"
            ) or generated_download(p.relative_to(ROOT)):
                continue
            paths.append(p)
    return paths


def inspect(files):
    issues = []
    for p in files:
        rel = p.relative_to(ROOT)
        if p.is_symlink() or not p.resolve().is_relative_to(ROOT.resolve()):
            issues.append(dict(file=rel.as_posix(), reason="external_or_linked_source"))
            continue
        if any(part in PRIVATE | IGNORED for part in rel.parts) or generated_download(
            rel
        ):
            issues.append(
                dict(file=rel.as_posix(), reason="private_or_generated_directory")
            )
        if (
            p.name.startswith(".env") and p.name != ".env.example"
        ) or p.suffix.lower() in (
            ".mp4",
            ".wav",
            ".mp3",
            ".mkv",
            ".pt",
            ".ckpt",
            ".pth",
            ".safetensors",
            ".pem",
            ".key",
        ):
            issues.append(dict(file=rel.as_posix(), reason="private_or_binary_file"))
        if p.stat().st_size > 5 * 1024 * 1024:
            issues.append(
                dict(file=rel.as_posix(), reason="unexpected_large_source_file")
            )
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeError:
            issues.append(dict(file=rel.as_posix(), reason="unexpected_binary_file"))
            continue
        if SECRET.search(text):
            issues.append(dict(file=rel.as_posix(), reason="possible_secret"))
        if re.search(r"(?:F:[/\\]lywproject|C:[/\\]Users[/\\]31245)", text, re.I):
            issues.append(dict(file=rel.as_posix(), reason="development_machine_path"))
    return issues


def source_files():
    """Review existing public files, including freshly edited untracked sources."""
    return sorted(p for p in public_files() if p.is_file())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", type=Path)
    args = parser.parse_args()
    files = public_files()
    issues = inspect(files)
    result = dict(
        status="passed" if not issues else "failed",
        checked_files=len(files),
        issues=issues,
        note="Source-only scan; not a guarantee against every possible secret format",
    )
    if args.json:
        args.json.write_text(
            json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(result, ensure_ascii=False))
    if issues:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
