"""Inputs shared by task handlers; each invocation takes a project snapshot."""

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..service import Projects


@dataclass(frozen=True)
class TaskContext:
    projects: "Projects"
    pid: str
    action: str
    payload: dict
    project: dict
    folder: Path
    media: dict | None = None
