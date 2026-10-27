"""Download the frozen Windows CUDA wheels in verified resumable HTTP ranges."""

from http.client import IncompleteRead
import json
import re
import sys
import time
import urllib.error
import urllib.request

from download_models import (
    ROOT,
    candidate_urls,
    download_retries,
    download_timeout,
    retry_delay,
    source_unusable,
    digest,
    download,
    installation_lock,
    _download_one,
)
from python_support import PYTHON_LABEL, SUPPORTED_PYTHON


def ranged_download(url, target, expected):
    last_error = None
    for candidate in candidate_urls(url):
        print(f"  Source: {candidate.split('/')[2]}", flush=True)
        try:
            _ranged_download_one(candidate, target, expected)
            return
        except (OSError, ValueError, RuntimeError, IncompleteRead) as error:
            last_error = error
            print("  PyTorch source failed; trying the next source", flush=True)
    raise last_error


def _ranged_download_one(url, target, expected):
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and digest(target) == expected:
        print("  Wheel already verified", flush=True)
        return
    part = target.with_name(target.name + ".part")
    retries = download_retries()
    for attempt in range(retries + 1):
        try:
            if part.exists() and digest(part) == expected:
                part.replace(target)
                return
            while True:
                offset = part.stat().st_size if part.exists() else 0
                end = offset + 32 * 1024 * 1024 - 1
                # A unique query avoids intermediaries serving another cached Range.
                ranged_url = (
                    url
                    + ("&" if "?" in url else "?")
                    + f"lyriframe_range={offset}-{end}"
                )
                request = urllib.request.Request(
                    ranged_url,
                    headers={
                        "User-Agent": "LyriFrame-Kim/1.3",
                        "Accept-Encoding": "identity",
                        "Range": f"bytes={offset}-{end}",
                    },
                )
                with urllib.request.urlopen(
                    request, timeout=download_timeout()
                ) as response:
                    match = re.fullmatch(
                        r"bytes (\d+)-(\d+)/(\d+)",
                        response.headers.get("Content-Range", ""),
                    )
                    if response.status == 200:
                        # Some mirrors ignore Range. Stream the same verified asset instead.
                        response.close()
                        _download_one(url, target, expected)
                        return
                    if response.status != 206 or not match:
                        raise ValueError(
                            "PyTorch download server did not return a valid byte range"
                        )
                    start, stop, total = map(int, match.groups())
                    if start != offset or stop != min(end, total - 1):
                        raise ValueError(
                            "PyTorch download returned a different byte range"
                        )
                    received = 0
                    with part.open("ab") as stream:
                        while block := response.read(1024 * 1024):
                            stream.write(block)
                            received += len(block)
                    if received != stop - start + 1:
                        raise OSError("Incomplete PyTorch byte range")
                print(
                    f"  {stop + 1:.0f} / {total:.0f} bytes ({(stop + 1) / total:.0%})",
                    flush=True,
                )
                if stop + 1 == total:
                    break
            if digest(part) != expected:
                part.unlink()
                raise ValueError("PyTorch wheel SHA256 mismatch")
            part.replace(target)
            return
        except (OSError, ValueError, IncompleteRead) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code == 416:
                part.unlink(missing_ok=True)
            if attempt == retries or source_unusable(error):
                raise
            delay = retry_delay(attempt)
            print(
                f"  {type(error).__name__}: {error}. PyTorch retry {attempt + 1}/{retries} in {delay}s",
                flush=True,
            )
            time.sleep(delay)


def selected_assets(version=None):
    version = version or sys.version_info[:2]
    if f"{version[0]}.{version[1]}" not in SUPPORTED_PYTHON:
        raise ValueError(f"{PYTHON_LABEL} is required for the CUDA wheel set")
    tag = f"cp{version[0]}{version[1]}"
    assets = json.loads(
        (ROOT / "requirements/wheels.lock.json").read_text(encoding="utf-8")
    )
    selected = [
        asset for asset in assets if f"-{tag}-{tag}-win_amd64.whl" in asset["name"]
    ]
    if len(selected) != 2 or {a["name"].split("-")[0] for a in selected} != {
        "torch",
        "torchaudio",
    }:
        raise ValueError(f"Incomplete CUDA wheel lock for {tag}")
    return selected


def main():
    assets = selected_assets()
    with installation_lock(ROOT):
        for asset in assets:
            print(asset["name"], flush=True)
            target = ROOT / ".runtime/wheels" / asset["name"]
            if asset["name"].startswith("torch-"):
                ranged_download(asset["url"], target, asset["sha256"])
            else:
                download(asset["url"], target, asset["sha256"])


if __name__ == "__main__":
    main()
