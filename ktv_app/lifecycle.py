"""Identify the local server and keep Windows model workers in its process job."""

import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import sys

_job_handle = None


def _kernel():
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
    ]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [
        ctypes.POINTER(wintypes.FILETIME)
    ] * 4
    return kernel


def windows_process_job():
    """The OS closes this handle on server exit, terminating all descendant workers."""
    global _job_handle
    if os.name != "nt" or _job_handle:
        return

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("process_time", ctypes.c_int64),
            ("job_time", ctypes.c_int64),
            ("flags", wintypes.DWORD),
            ("min_working_set", ctypes.c_size_t),
            ("max_working_set", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority", wintypes.DWORD),
            ("scheduling", wintypes.DWORD),
        ]

    class Limits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io_counters", ctypes.c_uint64 * 6),
            ("process_memory", ctypes.c_size_t),
            ("job_memory", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel = _kernel()
    handle = kernel.CreateJobObjectW(None, None)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    limits = Limits()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel.SetInformationJobObject(
        handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)
    ) or not kernel.AssignProcessToJobObject(handle, kernel.GetCurrentProcess()):
        error = ctypes.WinError(ctypes.get_last_error())
        kernel.CloseHandle(handle)
        raise error
    # Do not explicitly close a job containing this process during Python cleanup.
    # Windows releases the handle after the process exits.
    _job_handle = handle


def instance_record(root, port):
    creation = None
    executable = str(Path(sys.executable).resolve())
    if os.name == "nt":
        kernel = _kernel()
        buffer = ctypes.create_unicode_buffer(32768)
        kernel.GetModuleFileNameW.argtypes = [
            wintypes.HMODULE,
            wintypes.LPWSTR,
            wintypes.DWORD,
        ]
        if not kernel.GetModuleFileNameW(None, buffer, len(buffer)):
            raise ctypes.WinError(ctypes.get_last_error())
        executable = buffer.value
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(
            kernel.GetCurrentProcess(), *(ctypes.byref(t) for t in times)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        creation = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return dict(
        pid=os.getpid(),
        creation_filetime=creation,
        root=str(Path(root).resolve()),
        executable=executable,
        app_python=str(Path(sys.executable).resolve()),
        port=port,
    )


def register_instance(root, port):
    path = Path(root) / ".runtime" / f"server-{port}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(instance_record(root, port)), encoding="utf-8")
    temporary.replace(path)
    return path


def unregister_instance(path):
    try:
        if json.loads(path.read_text(encoding="utf-8")).get("pid") == os.getpid():
            path.unlink()
    except (OSError, ValueError):
        pass
