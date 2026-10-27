"""Shared interpreter policy for dependency selection and diagnostics."""

import json
from pathlib import Path

POLICY = json.loads(
    (
        Path(__file__).resolve().parents[1] / "requirements/python-compatibility.json"
    ).read_text(encoding="utf-8")
)
SUPPORTED_PYTHON = tuple(POLICY["versions"])
PYTHON_LABEL = "Python " + ", ".join(SUPPORTED_PYTHON) + " (64-bit)"
