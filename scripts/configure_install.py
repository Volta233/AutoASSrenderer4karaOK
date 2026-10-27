"""Record a CUDA installation without exposing or replacing private API settings."""

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sys

try:
    import tomllib
except ModuleNotFoundError:
    import tomli as tomllib

ROOT = Path(__file__).resolve().parents[1]


def update_values(text, values):
    for key, value in values.items():
        pattern = re.compile(rf"^{re.escape(key)}=.*$", re.M)
        replacement = f"{key}={value}"
        if pattern.search(text):
            text = pattern.sub(lambda _: replacement, text)
        else:
            text = text.rstrip() + "\n" + replacement + "\n"
    return text


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--torch-index", required=True)
    args = parser.parse_args()
    target = ROOT / ".env"
    original = (
        target.read_text(encoding="utf-8-sig")
        if target.exists()
        else (ROOT / ".env.example").read_text(encoding="utf-8")
    )
    target.write_text(
        update_values(
            original,
            {"KTV_DEVICE": "cuda", "TIFA_DEVICE": "cuda", "KTV_PORT": "8768"},
        ),
        encoding="utf-8",
    )
    runtime = ROOT / ".runtime"
    runtime.mkdir(exist_ok=True)
    (runtime / "installation.json").write_text(
        json.dumps(
            {
                "project": "LyriFrame",
                "version": tomllib.loads(
                    (ROOT / "pyproject.toml").read_text(encoding="utf-8")
                )["project"]["version"],
                "python": f"{sys.version_info.major}.{sys.version_info.minor}",
                "device_preference": "cuda",
                "wheel_device": "cuda",
                "torch_version": "2.11.0",
                "torchaudio_version": "2.11.0",
                "torch_index": args.torch_index,
                "configured_at": datetime.now(timezone.utc).isoformat(),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print("Configured CUDA installation; private settings preserved")


if __name__ == "__main__":
    main()
