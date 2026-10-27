"""Bounded-memory SHA256 hashing, including Python 3.10 runtimes."""

import hashlib
from pathlib import Path


def file_sha256(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            result.update(block)
    return result.hexdigest()
