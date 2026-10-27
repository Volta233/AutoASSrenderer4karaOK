"""JSON persistence shared by project editing and background tasks."""

import json
import os
import time
from pathlib import Path


def read_json(path):
    # Windows may briefly deny a read while an atomic replacement is completing.
    for attempt in range(4):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except PermissionError:
            if attempt == 3:
                raise
            time.sleep(0.02)


def write_json(path, value):
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
