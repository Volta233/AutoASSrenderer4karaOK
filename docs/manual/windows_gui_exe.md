# Windows GUI exe

This project supports a clickable Windows GUI launcher at:

```text
dist/LyricAlignmentGUI/LyricAlignmentGUI.exe
```

The exe is intentionally a lightweight GUI launcher. It opens the Tkinter GUI without a terminal window, but the actual karaoke generation job still uses the Python interpreter selected in the GUI's "Python" field. That Python environment must contain the Qwen Forced Aligner, Demucs, FFmpeg-related runtime dependencies, and any local model requirements used by the batch pipeline.

## Build

From the project root:

```powershell
python -m pip install -e .[gui-exe]
python scripts\windows\build_gui_exe.py --clean
```

You can also double-click or run:

```text
scripts\windows\build_gui_exe.bat
```

The build script creates:

```text
dist/LyricAlignmentGUI/LyricAlignmentGUI.exe
```

## Run

Double-click `dist\LyricAlignmentGUI\LyricAlignmentGUI.exe`.

If the exe cannot find the project root, set `LYRICALIGN_ROOT` to the directory that contains `src\` and `scripts\`:

```powershell
$env:LYRICALIGN_ROOT = "D:\path\to\LyricAlignment"
dist\LyricAlignmentGUI\LyricAlignmentGUI.exe
```

This is useful if you copy the exe folder away from the repository. For the normal in-repository build output, root discovery works automatically.

## Notes

- The exe does not bundle large model files or checkpoints.
- Default model paths still point under `models\` in the project root.
- To use a different generation environment, change the GUI's Python field or set `LYRICALIGN_PIPELINE_PYTHON` before launching.
