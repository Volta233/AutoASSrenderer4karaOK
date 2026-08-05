# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import sys


project_root = Path(SPECPATH).parents[1]
python_root = Path(sys.prefix)
tcl_tk_bin = python_root / "Library" / "bin"
tcl_tk_lib = python_root / "Library" / "lib"

tcl_tk_binaries = []
for name in ("tcl86t.dll", "tk86t.dll"):
    path = tcl_tk_bin / name
    if path.is_file():
        tcl_tk_binaries.append((str(path), "."))

tcl_tk_datas = []
for source, target in (
    (tcl_tk_lib / "tcl8.6", "_tcl_data"),
    (tcl_tk_lib / "tk8.6", "_tk_data"),
    (tcl_tk_lib / "tcl8", "tcl8"),
):
    if source.is_dir():
        tcl_tk_datas.append((str(source), target))

block_cipher = None

a = Analysis(
    [str(project_root / "scripts" / "gui" / "run_gui.py")],
    pathex=[str(project_root / "src")],
    binaries=tcl_tk_binaries,
    datas=tcl_tk_datas,
    hiddenimports=[
        "tkinter",
        "tkinter.colorchooser",
        "tkinter.filedialog",
        "tkinter.messagebox",
        "tkinter.scrolledtext",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="LyricAlignmentGUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="LyricAlignmentGUI",
)
