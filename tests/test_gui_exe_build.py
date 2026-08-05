from __future__ import annotations

import sys
from pathlib import Path

from lyricalign.gui import command as gui_command
from scripts.windows import build_gui_exe


def test_find_project_root_accepts_root_with_scripts_and_src(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "LyricAlignment"
    batch = root / "scripts" / "demo" / "run_qwen_fa_batch.py"
    app = root / "src" / "lyricalign" / "gui" / "app.py"
    batch.parent.mkdir(parents=True)
    app.parent.mkdir(parents=True)
    batch.write_text("", encoding="utf-8")
    app.write_text("", encoding="utf-8")

    monkeypatch.setattr(gui_command, "_candidate_project_roots", lambda: [tmp_path, root])

    assert gui_command.find_project_root() == root


def test_build_gui_exe_command_points_to_pyinstaller_spec() -> None:
    command = build_gui_exe.build_command(clean=True)

    assert command[:4] == [sys.executable, "-m", "PyInstaller", "--noconfirm"]
    assert "--clean" in command
    assert command[-1].endswith(str(Path("scripts") / "windows" / "LyricAlignmentGUI.spec"))


def test_build_gui_exe_environment_prefers_current_python_tcl_dlls() -> None:
    environment = build_gui_exe.build_environment()

    path_parts = environment["PATH"].split(";")

    assert path_parts[0].endswith(str(Path("Library") / "bin"))
