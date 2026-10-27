"""Install frozen upstream assets. Standard library only; never loads a model."""

import argparse
from contextlib import contextmanager
import hashlib
from http.client import IncompleteRead
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import tempfile
import time
import urllib.error
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def stream_digest(stream):
    result = hashlib.sha256()
    for block in iter(lambda: stream.read(1024 * 1024), b""):
        result.update(block)
    return result.hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return stream_digest(stream)


def within(root, relative):
    relative = PurePosixPath(relative)
    if (
        relative.is_absolute()
        or ".." in relative.parts
        or "\\" in str(relative)
        or ":" in str(relative)
    ):
        raise ValueError("Unsafe asset path")
    target = root.joinpath(*relative.parts).resolve()
    if not target.is_relative_to(root.resolve()):
        raise ValueError("Asset leaves installation directory")
    return target


def source_profile():
    value = os.environ.get("LYRIFRAME_DOWNLOAD_SOURCE", "cn")
    if value not in {"cn", "official"}:
        raise ValueError("LYRIFRAME_DOWNLOAD_SOURCE must be cn or official")
    return value


def download_retries():
    return _download_setting("LYRIFRAME_DOWNLOAD_RETRIES", 10, 0, 100)


def download_timeout():
    return _download_setting("LYRIFRAME_DOWNLOAD_TIMEOUT", 90, 10, 600)


def _download_setting(name, default, minimum, maximum):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError as error:
        raise ValueError(
            f"{name} must be an integer from {minimum} to {maximum}"
        ) from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be from {minimum} to {maximum}")
    return value


def retry_delay(attempt):
    return min(2 ** min(attempt + 1, 5), 30)


def source_unusable(error):
    # Missing/forbidden files and different bytes are not repaired by waiting.
    return (
        isinstance(error, urllib.error.HTTPError)
        and error.code in {400, 401, 403, 404, 410}
    ) or (isinstance(error, ValueError) and "SHA256 mismatch" in str(error))


def candidate_urls(url):
    """Route immutable public assets only; retain the official URL as fallback."""
    from urllib.parse import urlsplit

    if source_profile() == "official":
        return [url]
    parsed = urlsplit(url)
    host, path = parsed.hostname, parsed.path
    mirrors = []
    if host == "huggingface.co":
        mirrors = [url.replace("https://huggingface.co/", "https://hf-mirror.com/", 1)]
        prefix = "/Qwen/Qwen3-ASR-1.7B/resolve/"
        if path.startswith(prefix):
            # Pin the ModelScope snapshot as well as the upstream content hash.
            filename = path[len(prefix) :].split("/", 1)[1]
            mirrors.insert(
                0,
                "https://modelscope.cn/models/Qwen/Qwen3-ASR-1.7B/resolve/a04930dbe5419bfee073f7cade734f572689a3a8/"
                + filename,
            )
        if (
            path
            == "/KimberleyJSN/melbandroformer/resolve/ac9b0614ab3cd7f77219e18ba494dfd93956c348/MelBandRoformer.ckpt"
        ):
            mirrors.insert(
                0,
                "https://modelscope.cn/models/cpadyun/melband-roformer/resolve/2fb90062313e164074d3d943fd133e5d7295ceb2/MelBandRoformer.ckpt",
            )
    elif host == "codeload.github.com":
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[2] == "zip":
            owner, repo, _, revision = parts
            mirrors = [
                f"https://ghfast.top/https://github.com/{owner}/{repo}/archive/{revision}.zip"
            ]
    elif host in {"github.com", "raw.githubusercontent.com"}:
        mirrors = ["https://ghfast.top/" + url]
    elif host in {
        "download.pytorch.org",
        "download-r2.pytorch.org",
    } and path.startswith("/whl/"):
        suffix = path.removeprefix("/whl/")
        mirrors = [
            "https://mirrors.aliyun.com/pytorch-wheels/" + suffix,
            "https://mirror.sjtu.edu.cn/pytorch-wheels/" + suffix,
        ]
    elif host == "files.pythonhosted.org":
        mirrors = ["https://pypi.tuna.tsinghua.edu.cn" + path]
    return list(dict.fromkeys([*mirrors, url]))


def download(url, destination, expected):
    """Try mirrors sequentially, preserving verified bytes and interrupted data."""
    from urllib.parse import urlsplit

    last_error = None
    for candidate in candidate_urls(url):
        print(f"  Source: {urlsplit(candidate).hostname}", flush=True)
        try:
            _download_one(candidate, destination, expected)
            return
        except RuntimeError as error:
            last_error = error
            print("  Source failed; trying the next available source", flush=True)
    raise RuntimeError(
        f"Download failed: {destination.name}. Run install.cmd again to resume."
    ) from last_error


def _download_one(url, destination, expected):
    """Keep interrupted bytes, validate SHA256 before making a file usable."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file() and digest(destination) == expected:
        return
    part = destination.with_name(destination.name + ".part")
    retries = download_retries()
    for attempt in range(retries + 1):
        try:
            offset = part.stat().st_size if part.exists() else 0
            headers = {"User-Agent": "LyriFrame-Kim/1.3", "Accept-Encoding": "identity"}
            if offset:
                headers["Range"] = f"bytes={offset}-"
            request = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(
                request, timeout=download_timeout()
            ) as response:
                resumed = response.status == 206
                if resumed and not response.headers.get("Content-Range", "").startswith(
                    f"bytes {offset}-"
                ):
                    raise ValueError("Invalid download resume response")
                if not resumed:
                    offset = 0
                total = int(response.headers.get("Content-Length", "0")) + offset
                copied, last_notice = offset, time.monotonic()
                with part.open("ab" if resumed else "wb") as stream:
                    while block := response.read(1024 * 1024):
                        stream.write(block)
                        copied += len(block)
                        if time.monotonic() - last_notice >= 5:
                            print(
                                f"  {copied / 1048576:.0f} / {total / 1048576:.0f} MB",
                                flush=True,
                            )
                            last_notice = time.monotonic()
            if digest(part) != expected:
                part.unlink()
                raise ValueError("Download SHA256 mismatch; the file will not be used")
            part.replace(destination)
            return
        except (OSError, ValueError, IncompleteRead) as error:
            if isinstance(error, urllib.error.HTTPError) and error.code == 416:
                # The server may reject Range when the entire file arrived before an interruption.
                if part.exists() and digest(part) == expected:
                    part.replace(destination)
                    return
                part.unlink(missing_ok=True)
            if attempt == retries or source_unusable(error):
                raise RuntimeError(
                    f"Download failed: {destination.name}. Run install.cmd again to resume."
                ) from error
            delay = retry_delay(attempt)
            print(
                f"  {type(error).__name__}: {error}. Retry {attempt + 1}/{retries} in {delay}s; partial data preserved",
                flush=True,
            )
            time.sleep(delay)


def extract(archive, destination, strip):
    """Stage a verified archive and reject traversal, links and duplicate names."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    hashes = {}
    with tempfile.TemporaryDirectory(
        prefix="lyriframe-assets-", dir=destination.parent
    ) as temp:
        staged = Path(temp)
        with zipfile.ZipFile(archive) as bundle:
            for member in bundle.infolist():
                # Validate even directories and members excluded by strip.
                within(staged, member.filename)
                if (member.external_attr >> 16) & 0o170000 == 0o120000:
                    raise ValueError("Archive links are not allowed")
                parts = PurePosixPath(member.filename).parts[strip:]
                if member.is_dir() or not parts:
                    continue
                name = PurePosixPath(*parts).as_posix()
                if name in hashes:
                    raise ValueError("Duplicate archive member")
                target = within(staged, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(member) as source, target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                hashes[name] = digest(target)
        if not hashes:
            raise ValueError("Archive has no usable files")
        for name in hashes:
            target = within(destination, name)
            target.parent.mkdir(parents=True, exist_ok=True)
            os.replace(within(staged, name), target)
    return hashes


@contextmanager
def installation_lock(root):
    """OS lock is released even after a crash; stale lock files do not block repair."""
    folder = root / ".runtime"
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / "asset-install.lock").open("a+b") as stream:
        stream.seek(0)
        stream.write(b"0")
        stream.flush()
        stream.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def install(root, assets):
    root = root.resolve()
    cache = root / ".runtime/downloads"
    receipt = root / ".runtime/assets-installed.json"
    saved = json.loads(receipt.read_text(encoding="utf-8")) if receipt.exists() else {}
    for index, asset in enumerate(assets, 1):
        target = within(root, asset["target"])
        print(f"[{index}/{len(assets)}] {asset['name']}", flush=True)
        if asset["kind"] == "file":
            download(asset["url"], target, asset["sha256"])
            continue
        previous = saved.get(asset["name"], {})
        hashes = previous.get("files", {})
        if (
            previous.get("sha256") == asset["sha256"]
            and hashes
            and all(
                within(target, name).is_file()
                and digest(within(target, name)) == expected
                for name, expected in hashes.items()
            )
        ):
            print("  Verified; already installed", flush=True)
            continue
        archive = cache / asset["name"]
        download(asset["url"], archive, asset["sha256"])
        hashes = extract(archive, target, asset["strip"])
        saved[asset["name"]] = dict(sha256=asset["sha256"], files=hashes)
        receipt.parent.mkdir(parents=True, exist_ok=True)
        temporary = receipt.with_suffix(".tmp")
        temporary.write_text(json.dumps(saved, indent=2), encoding="utf-8")
        temporary.replace(receipt)
        # Avoid retaining the 500 MB dictionary archive after a successful install.
        archive.unlink()
    unidic = root / "models/forced_alignment/tifa/resources/UniDic/unidic"
    if (unidic / "sys.dic").exists() and not (unidic / "mecabrc").exists():
        (unidic / "mecabrc").write_text(
            "# Local UniDic configuration\n", encoding="utf-8"
        )
    print("Models and FFmpeg verified. No Qwen forced aligner installed.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--source", choices=("cn", "official"), default=None)
    parser.add_argument("--retries", type=int, default=None)
    parser.add_argument("--timeout", type=int, default=None)
    parser.add_argument(
        "--plan", action="store_true", help="Show downloads without writing files"
    )
    args = parser.parse_args()
    if args.source:
        os.environ["LYRIFRAME_DOWNLOAD_SOURCE"] = args.source
    if args.retries is not None:
        os.environ["LYRIFRAME_DOWNLOAD_RETRIES"] = str(args.retries)
    if args.timeout is not None:
        os.environ["LYRIFRAME_DOWNLOAD_TIMEOUT"] = str(args.timeout)
    download_retries()
    download_timeout()
    assets = json.loads(
        (ROOT / "requirements/assets.lock.json").read_text(encoding="utf-8")
    )["assets"]
    if args.plan:
        for asset in assets:
            print(asset["target"])
        return
    with installation_lock(args.root):
        install(args.root, assets)


if __name__ == "__main__":
    main()
