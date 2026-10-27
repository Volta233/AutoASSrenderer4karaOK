"""Install a verified pip wheel without using an old pip's HTTPS stack."""

import importlib.metadata
import json
import subprocess
import sys

from download_models import ROOT, download, installation_lock


def main():
    asset = json.loads(
        (ROOT / "requirements/bootstrap.lock.json").read_text(encoding="utf-8")
    )
    try:
        installed = importlib.metadata.version("pip")
    except importlib.metadata.PackageNotFoundError:
        subprocess.run([sys.executable, "-m", "ensurepip", "--upgrade"], check=True)
        installed = None
    if installed == asset["version"]:
        print("Package installer ready", flush=True)
        return
    target = ROOT / ".runtime/wheels" / asset["name"]
    with installation_lock(ROOT):
        download(asset["url"], target, asset["sha256"])
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-index",
            "--disable-pip-version-check",
            str(target),
        ],
        check=True,
    )


if __name__ == "__main__":
    main()
