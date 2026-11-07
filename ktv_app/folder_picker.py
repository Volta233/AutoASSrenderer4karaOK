"""Native folder selection for settings that hold a local path.

This is a local application, so the browser cannot name a folder on the machine
itself. Instead the page asks the server to open the system dialog on the same
desktop the user is looking at; the chosen path is returned and can be edited
before it is saved.
"""

import os
import shutil
import subprocess
import threading
from pathlib import Path

LOCK = threading.Lock()

WINDOWS_SCRIPT = r"""
Add-Type -AssemblyName System.Windows.Forms
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$dialog = New-Object System.Windows.Forms.FolderBrowserDialog
$dialog.Description = $env:KTV_PICK_TITLE
$dialog.ShowNewFolderButton = $true
if ($env:KTV_PICK_INITIAL) { $dialog.SelectedPath = $env:KTV_PICK_INITIAL }
if ($dialog.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) {
    Write-Output $dialog.SelectedPath
}
"""


def _ask_tk(title, initial):
    """Use the dialog bundled with CPython; no console window and no extra install."""
    import tkinter
    from tkinter import filedialog

    root = tkinter.Tk()
    try:
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except tkinter.TclError:
            pass
        root.update()
        return filedialog.askdirectory(
            parent=root,
            title=title,
            initialdir=initial or None,
            mustexist=False,
        )
    finally:
        root.destroy()


def _ask_windows(title, initial):
    """Fall back to the WinForms dialog when Tk is unavailable in this Python."""
    configured = shutil.which("powershell") or shutil.which("pwsh")
    if not configured:
        raise FileNotFoundError("缺少 powershell，无法打开文件夹选择窗口")
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    result = subprocess.run(
        [configured, "-NoProfile", "-STA", "-Command", WINDOWS_SCRIPT],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={
            **os.environ,
            "KTV_PICK_TITLE": title,
            "KTV_PICK_INITIAL": initial or "",
        },
        creationflags=flags,
        timeout=900,
    )
    return result.stdout.strip()


def pick_directory(title="选择文件夹", initial=""):
    """Open one folder dialog and return the chosen path, or None when cancelled."""
    if not LOCK.acquire(blocking=False):
        raise ValueError("桌面上已有一个选择窗口，请先完成它")
    try:
        try:
            chosen = _ask_tk(title, initial)
        except Exception:
            if os.name != "nt":
                raise ValueError("无法打开系统的文件夹选择窗口，请直接粘贴路径")
            chosen = _ask_windows(title, initial)
    except (OSError, subprocess.SubprocessError) as error:
        raise ValueError("无法打开系统的文件夹选择窗口，请直接粘贴路径") from error
    finally:
        LOCK.release()
    text = str(chosen or "").strip()
    return str(Path(text)) if text else None
