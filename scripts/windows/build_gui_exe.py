from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = ROOT / "scripts" / "windows" / "LyricAlignmentGUI.spec"


def build_command(*, clean: bool) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
    ]
    if clean:
        command.append("--clean")
    command.append(str(SPEC))
    return command


def build_environment() -> dict[str, str]:
    environment = os.environ.copy()
    path_prefixes = [
        Path(sys.prefix) / "Library" / "bin",
        Path(sys.prefix) / "DLLs",
        Path(sys.prefix) / "Scripts",
    ]
    existing_path = environment.get("PATH", "")
    environment["PATH"] = os.pathsep.join(str(path) for path in path_prefixes if path.exists())
    if existing_path:
        environment["PATH"] = f"{environment['PATH']}{os.pathsep}{existing_path}"
    return environment


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the clickable LyricAlignment GUI exe.")
    parser.add_argument("--clean", action="store_true", help="Ask PyInstaller to ignore cached build state.")
    parser.add_argument("--print-command", action="store_true", help="Print the PyInstaller command without running it.")
    args = parser.parse_args(argv)

    command = build_command(clean=args.clean)
    if args.print_command:
        print(subprocess.list2cmdline(command))
        return 0

    try:
        import PyInstaller  # noqa: F401
    except ModuleNotFoundError:
        print(
            "PyInstaller is not installed in this Python environment.\n"
            "Install it with:\n"
            f"  {sys.executable} -m pip install pyinstaller\n"
            "Then rerun this build script.",
            file=sys.stderr,
        )
        return 2

    completed = subprocess.run(command, cwd=str(ROOT), env=build_environment(), check=False)
    if completed.returncode:
        return completed.returncode

    exe = ROOT / "dist" / "LyricAlignmentGUI" / "LyricAlignmentGUI.exe"
    print(f"Built: {exe}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
