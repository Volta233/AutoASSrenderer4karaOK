"""User-facing preferences kept beside project records.

These values change behaviour for the whole application (for example where a
finished video is saved), so they are validated once when saved instead of on
every task run.
"""

import threading
from pathlib import Path

from .storage import read_json, write_json


def absolute_directory(value):
    """Normalise a user-entered folder and create it when it is missing."""
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        raise ValueError("请填写完整路径，例如 D:\\歌曲\\成品")
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise ValueError("该路径是已有文件，请选择一个文件夹")
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise ValueError(f"无法使用该文件夹：{error}") from error
    return str(path)


class Preferences:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.lock = threading.RLock()

    def read(self):
        with self.lock:
            values = read_json(self.path) if self.path.is_file() else {}
        return {"download_dir": str(values.get("download_dir") or "")}

    def save(self, **fields):
        values = self.read()
        if "download_dir" in fields:
            values["download_dir"] = absolute_directory(fields["download_dir"])
        with self.lock:
            write_json(self.path, values)
        return values

    def download_dir(self):
        value = self.read()["download_dir"]
        return Path(value) if value else None
