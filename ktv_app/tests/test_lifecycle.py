"""Exercise only owned, isolated server/helper processes; never run model inference."""

import ctypes
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time

import httpx
import pytest

from ktv_app.lifecycle import instance_record, register_instance, unregister_instance

ROOT = Path(__file__).resolve().parents[2]
WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows process lifecycle")


def wait_for(check, timeout=20):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise AssertionError("Owned process did not reach the expected state")


def test_instance_cleanup_does_not_delete_a_replacement_record(tmp_path):
    record = instance_record(tmp_path, 12345)
    assert record["pid"] == os.getpid()
    assert record["root"] == str(tmp_path.resolve())
    path = register_instance(tmp_path, 12345)
    other = dict(record, pid=-1)
    path.write_text(json.dumps(other), encoding="utf-8")
    unregister_instance(path)
    assert path.exists()
    path.write_text(json.dumps(record), encoding="utf-8")
    unregister_instance(path)
    assert not path.exists()


@WINDOWS
@pytest.mark.skipif(
    Path(sys.executable).resolve()
    != (ROOT / ".runtime/app/Scripts/python.exe").resolve(),
    reason="Requires the installed Windows application environment",
)
def test_stop_entrypoint_checks_pid_identity_and_stops_real_server(tmp_path):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    record_path = ROOT / ".runtime" / f"server-{port}.json"
    env = dict(os.environ, KTV_PORT=str(port), KTV_PROJECTS=str(tmp_path / "projects"))
    with (tmp_path / "server.log").open("w", encoding="utf-8") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "ktv_app.serve"],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        try:

            def ready():
                try:
                    return (
                        httpx.get(
                            f"http://127.0.0.1:{port}/api/projects", timeout=1
                        ).status_code
                        == 200
                    )
                except httpx.HTTPError:
                    return False

            wait_for(ready)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            duplicate = subprocess.run(
                [sys.executable, "-m", "ktv_app.serve"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                timeout=15,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            assert duplicate.returncode != 0
            assert json.loads(record_path.read_text(encoding="utf-8")) == record
            assert ready()
            # A stale/reused PID must not terminate even this known, owned process.
            record_path.write_text(
                json.dumps(dict(record, creation_filetime=1)), encoding="utf-8"
            )
            command = [
                "powershell.exe",
                "-NoProfile",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                str(ROOT / "stop.ps1"),
                "-Port",
                str(port),
            ]
            rejected = subprocess.run(command, capture_output=True, timeout=15)
            assert rejected.returncode == 1
            assert ready()
            record_path.write_text(json.dumps(record), encoding="utf-8")
            stopped = subprocess.run(command, capture_output=True, timeout=15)
            assert stopped.returncode == 0, stopped.stderr.decode(errors="replace")
            process.wait(timeout=10)
            assert not record_path.exists()
        finally:
            if process.poll() is None:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=10,
                )
                process.wait(timeout=10)


@WINDOWS
def test_server_job_terminates_descendant_when_only_server_is_killed(tmp_path):
    parent_file, child_file = tmp_path / "parent.json", tmp_path / "child.txt"
    child_code = f"import os,time; from pathlib import Path; Path({str(child_file)!r}).write_text(str(os.getpid())); time.sleep(60)"
    code = (
        "import subprocess,sys,time; from ktv_app.lifecycle import windows_process_job,register_instance; "
        "windows_process_job(); "
        f"register_instance({str(tmp_path)!r}, 12345).replace({str(parent_file)!r}); "
        f"subprocess.Popen([sys.executable,'-c',{child_code!r}],creationflags=subprocess.CREATE_NO_WINDOW); "
        "time.sleep(60)"
    )
    helper = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=ROOT,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    parent_pid = child_pid = None
    try:
        wait_for(lambda: parent_file.exists() and child_file.exists())
        parent_pid = json.loads(parent_file.read_text())["pid"]
        child_pid = int(child_file.read_text())
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_ulong]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        child_handle = kernel.OpenProcess(0x100000, False, child_pid)
        assert child_handle
        try:
            # No /T: descendant termination must come from the Windows job itself.
            os.kill(parent_pid, signal.SIGTERM)
            assert kernel.WaitForSingleObject(child_handle, 10000) == 0
        finally:
            kernel.CloseHandle(child_handle)
        helper.wait(timeout=10)
    finally:
        if helper.poll() is None:
            subprocess.run(
                ["taskkill.exe", "/PID", str(helper.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
            helper.wait(timeout=10)
