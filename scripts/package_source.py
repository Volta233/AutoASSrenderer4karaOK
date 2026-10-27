"""Package audited public source without models, tasks or development environments."""

import argparse
from pathlib import Path

from check_release import inspect, source_files
from package_common import ROOT, build_package


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = source_files()
    issues = inspect(paths)
    if not paths or issues:
        raise SystemExit(
            "Public-source audit failed; run scripts/check_release.py for filenames and reasons"
        )
    entries = {p.relative_to(ROOT).as_posix(): p for p in paths}
    build_package(
        entries,
        args.output,
        "source",
        dict(
            models_included=False,
            pipeline=["Kim Mel-Band Roformer", "Qwen3-ASR-1.7B", "TIFA-1.0-ST"],
            qwen_fa_included=False,
        ),
    )


if __name__ == "__main__":
    main()
