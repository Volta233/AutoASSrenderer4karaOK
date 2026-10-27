"""Install dependencies through one index at a time, with explicit fallback."""

import os
import subprocess
import sys

from download_models import source_profile, download_retries, download_timeout


def indexes():
    official = "https://pypi.org/simple"
    if source_profile() == "official":
        return [official]
    return [
        "https://pypi.tuna.tsinghua.edu.cn/simple",
        "https://mirrors.aliyun.com/pypi/simple",
        official,
    ]


def main():
    arguments = sys.argv[1:]
    if not arguments:
        raise SystemExit("Expected pip install arguments")
    environment = os.environ.copy()
    # External pip config must not silently introduce another index or CPU torch.
    for key in list(environment):
        if key.startswith("PIP_"):
            environment.pop(key)
    environment["PIP_CONFIG_FILE"] = os.devnull
    for index in indexes():
        print(f"Package source: {index}", flush=True)
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--disable-pip-version-check",
                "--timeout",
                str(download_timeout()),
                "--retries",
                str(download_retries()),
                "--resume-retries",
                str(download_retries()),
                "--index-url",
                index,
                *arguments,
            ],
            env=environment,
        )
        if result.returncode == 0:
            return
        print("Package source failed; trying the next source", flush=True)
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
