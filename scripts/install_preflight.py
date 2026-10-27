"""Check bundled resources before installing dependencies; standard library only."""

import argparse
from pathlib import Path

from doctor import check_resources, configuration


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    root = args.root.resolve()
    # g2p.local.yaml is generated later, after dependencies are installed.
    records = check_resources(root, configuration(root), check_local_paths=False)
    failed = [record for record in records if record["status"] != "passed"]
    if failed:
        print("Resources are incomplete. Run install.cmd to download and repair them.")
        for record in failed:
            print("- " + record["name"] + ": " + record["details"])
            for path in record.get("missing", []):
                print("  " + path)
        raise SystemExit(1)
    print("Model and tool resources are present. Ready to install dependencies.")


if __name__ == "__main__":
    main()
