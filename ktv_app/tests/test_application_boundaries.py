"""Regression checks across the extracted application and distribution boundaries."""

from pathlib import Path
import socket
import subprocess
import sys

import pytest

from ktv_app.backend.service import Projects
from ktv_app.backend.storage import read_json, write_json
from ktv_app.config import Settings
from ktv_app.timeline import Timeline

ROOT = Path(__file__).resolve().parents[2]


def project(tmp_path):
    store = Projects(Settings(projects=tmp_path / "projects"))
    video = tmp_path / "video.mp4"
    video.write_bytes(b"CRUD fixture; not passed to a model")
    return store, store.create(video, "Song", "Artist", "en")["id"]


def test_worker_failure_releases_busy_state_and_survives_restart(tmp_path, monkeypatch):
    store, pid = project(tmp_path)

    def fail(*args):
        raise RuntimeError("synthetic worker failure")

    monkeypatch.setattr("ktv_app.backend.service.execute", fail)
    store.submit(pid, "media")
    store.pool.shutdown(wait=True)
    persisted = read_json(store.folder(pid) / "project.json")
    assert persisted["busy"] is False
    assert persisted["stage"] == "failed"
    assert persisted["error"] == "synthetic worker failure"
    reopened = Projects(store.settings)
    try:
        assert reopened.get(pid)["error"] == persisted["error"]
    finally:
        reopened.pool.shutdown(wait=True)


def test_repository_recovers_only_running_records_and_preserves_timeline(tmp_path):
    store, pid = project(tmp_path)
    store.update(pid, busy=True, stage="align")
    timeline = Timeline(duration=30)
    write_json(store.folder(pid) / "timeline.json", timeline.model_dump())
    store.pool.shutdown(wait=True)
    reopened = Projects(store.settings)
    try:
        current = reopened.get(pid)
        assert not current["busy"] and current["stage"] == "interrupted"
        assert current["timeline"] == timeline.model_dump()
    finally:
        reopened.pool.shutdown(wait=True)


def test_source_only_install_preflight_is_actionable_and_creates_no_environment(
    tmp_path,
):
    root = tmp_path / "源码 with spaces"
    root.mkdir()
    result = subprocess.run(
        [
            sys.executable,
            "-X",
            "utf8",
            str(ROOT / "scripts/install_preflight.py"),
            "--root",
            str(root),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=45,
    )
    assert result.returncode == 1
    assert "install.cmd" in result.stdout
    assert "download and repair" in result.stdout
    assert "Traceback" not in result.stderr
    assert not (root / ".runtime").exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows launcher")
def test_launcher_refuses_an_occupied_port_without_starting_a_server():
    import shutil

    shell = shutil.which("powershell")
    if not shell or not (ROOT / ".runtime/app/Scripts/python.exe").exists():
        pytest.skip("Local installed Windows application required")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen()
        port = listener.getsockname()[1]
        result = subprocess.run(
            [
                shell,
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "start.ps1"),
                "-Port",
                str(port),
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
        assert result.returncode != 0
        assert "already in use" in result.stderr
        # Our listener still owns the port; the launcher did not stop it.
        assert listener.getsockname()[1] == port
