# GUI Karaoke Rendering Tool

This GUI is a thin, user-friendly wrapper around:

```text
scripts/demo/run_qwen_fa_batch.py
```

It lets a user pick an audio/MV file, provide lyrics, choose model/separator
settings, and generate the final MP4 with burned karaoke subtitles.

## 1. Open the GUI

From the project root:

```powershell
cd D:\小玩具\纯K工具v004\LyricAlignment
$env:PYTHONPATH = "src"
python scripts\gui\run_gui.py
```

Or use the bundled bash launcher from Git Bash or a configured WSL shell:

```bash
bash scripts/gui/run_gui.sh
```

On this Windows machine, `C:\Windows\system32\bash.exe` may point to WSL. If WSL
has no installed default distribution, use Git Bash or run the Python command
above directly.

The launcher sets `PYTHONPATH=src`, opens the GUI with
`D:/Anconda/envs/qwen312/python.exe`, and passes the same Python to the pipeline
subprocess through `LYRICALIGN_PIPELINE_PYTHON`. Override either path when
needed:

```bash
GUI_PYTHON="D:/Anconda/envs/qwen312/python.exe" \
LYRICALIGN_PIPELINE_PYTHON="D:/Anconda/envs/qwen312/python.exe" \
bash scripts/gui/run_gui.sh
```

The full pipeline requires `ffmpeg`, `ffprobe`, Qwen Forced Aligner
weights/checkpoints, and either Demucs or Spleeter for separated-vocal mode.
The GUI can be opened from any Python that has Tkinter, but the pipeline
subprocess defaults to:

```text
D:\Anconda\envs\qwen312\python.exe
```

Change **Python 解释器** if your working Qwen/Demucs environment is elsewhere.
The selected pipeline Python must use a Transformers source build that
recognizes Qwen Forced Aligner's `qwen3_asr` model type.

## 2. Basic Workflow

1. Select **音频 / MV**.
   Supported media is the same as the batch CLI: common video files such as
   `.mp4`, `.mkv`, `.mov`, and common audio files such as `.wav`, `.flac`,
   `.mp3`, `.m4a`.
2. Provide lyrics.
   Choose a TXT file, or paste lyrics into the text box. A selected TXT file
   takes priority over pasted text.
3. Select an output directory.
   If empty, the CLI default is used: `<source folder>/<media stem>_qwen_fa`.
4. Keep the default preset for normal use.
   `default` generates `R2 + separated vocal + windowed alignment`, then renders
   one final karaoke MP4.
5. Click **预检 dry-run** first.
   This checks discovery and command construction without loading models or
   writing the final output.
6. Click **开始生成成品**.
   Progress and JSON logs stream into the log panel. The final video is under
   the output directory's `videos/individual/` folder.

## 3. Main Options

- **语言**: Choose the singing language. Chinese is the default. Japanese
  requires `nagisa`.
- **阶段**: `all` runs prepare, align, and render. `prepare`, `align`, and
  `render` are useful for resuming one stage.
- **预设**: `default` is the practical one-video output. Comparison presets
  generate extra diagnostic videos and take longer.
- **成品音轨**: `source` keeps the original song/MV audio in the final video.
  `aligned` uses the alignment input, useful for diagnostics.
- **字幕字体**: Optional. Leave empty for automatic language-based selection.
  On Windows Chinese machines without `Noto Sans CJK SC`, use
  `Microsoft YaHei` or `Microsoft YaHei UI`.
- **字幕带高度**: Optional pixel height for the subtitle overlay area. Empty uses
  the automatic height. For video inputs this area is in the lower part of the
  original frame; it does not extend the canvas.
- **字幕颜色**: Click the color buttons to open the system color picker for
  highlight, unsung text, preview text, and outline.
- **音频背景图**: Optional image used as the video background when the input is
  audio-only. If empty, the renderer uses a dark default background instead of
  pure black.
- **分离器**: `demucs` is the default. `spleeter` remains available if its
  weights are already installed.
- **Python 解释器**: The Python used for the actual batch subprocess. It must
  have Qwen runtime dependencies and Demucs/Spleeter installed.
- **对齐设备 / Demucs 设备**: Defaults to `cpu` for local Windows compatibility.
  Select `cuda` only when `torch.cuda.is_available()` is true in the selected
  Python environment.
- **只用本地模型**: Adds `--local-files-only`, useful after Qwen and Demucs
  weights have already been cached.
- **重做准备 / 人声分离 / 对齐 / 渲染**: Force-refresh specific cached stages.

## 4. Demucs Weights

The project default is:

```text
demucs==4.1.0
model: htdemucs_ft
mode: --two-stems vocals
```

Official channels:

- Demucs upstream repository: `https://github.com/facebookresearch/demucs`
- HTDemucs fine-tuned weights page: `https://huggingface.co/adefossez/HTDemucs-ft`

Recommended usage is not to manually copy weights into this repository. Set a
cache directory in **Demucs 缓存**; the GUI passes it as `TORCH_HOME`, and Demucs
downloads/uses its model cache there on first execution. Keep this cache outside
Git, for example:

```text
D:\ASRModels\torch
```

If Demucs is installed in a separate conda environment, set **Demucs 命令** to:

```text
conda run -n demucs demucs
```

## 5. Qwen Model and Checkpoints

The CLI still controls Qwen Forced Aligner loading. If the server paths in the
script are not valid on this machine, fill in:

- **Qwen 模型路径**: local `Qwen3-ForcedAligner-0.6B-hf` snapshot.
- **R1 checkpoint** / **R2 checkpoint**: validation-selected checkpoint
  directories.
- **HF 缓存目录**: optional Hugging Face cache directory.

The GUI defaults are:

```text
Qwen 模型路径:
models/hf/Qwen3-ForcedAligner-0.6B-hf

R2 checkpoint:
models/R2checkpoint
```

Download or resume the official Qwen Forced Aligner base model with:

```powershell
python scripts\assets\download_qwen_model.py `
  --model-id Qwen/Qwen3-ForcedAligner-0.6B-hf `
  --revision c07281df297b9905d24a508279258cccf987a064 `
  --cache-dir models\hf_cache `
  --local-dir models\hf\Qwen3-ForcedAligner-0.6B-hf `
  --manifest models\manifests\qwen3_forced_aligner_0_6b_hf.json
```

This uses Hugging Face Hub snapshot caching. If interrupted, rerun the same
command to resume. The R2 LoRA/projector checkpoint is not part of the official
base model. This local GUI package expects it under:

```text
models/R2checkpoint/
  adapter/
  checkpoint_identity.json
  projector.pt
  trainer_state.pt
```

`projector.pt` must be directly inside `models/R2checkpoint`, not under
`models/checkpoints/...`. Choose a different R2 checkpoint directory in the GUI
only if it has the same structure.

## 5.1 Qwen Runtime Compatibility

If the log contains:

```text
ValueError: The checkpoint you are trying to load has model type `qwen3_asr`
but Transformers does not recognize this architecture.
```

the selected Python environment has an incompatible PyPI Transformers build.
Fix it in the same environment shown in **Python 解释器**:

```powershell
conda activate qwen312
python -m pip install peft
python -m pip install --upgrade "git+https://github.com/huggingface/transformers"
```

Verify:

```powershell
python -c "import transformers; print(transformers.__version__); from transformers import AutoConfig; c=AutoConfig.from_pretrained('models/hf/Qwen3-ForcedAligner-0.6B-hf'); print(c.model_type)"
```

Expected output includes:

```text
5.15.0.dev0
qwen3_asr
```

For the normal Chinese karaoke output, R2 is the default selected mode. If R2 is
missing, the command will fail during alignment until the checkpoint path is
provided.

If prepare fails during `separation_quality_check` with `missing audio file:
...\work\audio\mix.wav`, rerun with the same settings. The batch entry now
rebuilds a missing `mix.wav` from the original media and resumes from existing
Demucs stems instead of repeating the full separation.

The GUI page itself is scrollable. Use the mouse wheel to reach lower controls
on smaller screens.

## 5.2 Subtitle Fonts and Style

Rendering uses ASS subtitles and libass through FFmpeg, the same subtitle format
edited by Aegisub. The generated `.ass` file contains:

- `Preview`: the upcoming line.
- `Karaoke`: the active line with `\kf` timing.
- `Meta`: the small label in the subtitle band.

If `Noto Sans CJK SC` is not installed, the renderer no longer fails just
because that exact font is missing. It tries common CJK families first and logs
the selected fallback. For normal Windows Chinese output, `Microsoft YaHei` is
the practical manual choice.

Default colors:

```text
高亮: #FFE800
未唱: #909090
预览: #A0A0A0
描边: #101010
```

The GUI stores colors as `#RRGGBB` internally and converts them to ASS color
values before rendering. CLI users can still pass:

```powershell
--karaoke-primary-color "#FFE800"
--karaoke-secondary-color "#909090"
--subtitle-preview-color "#A0A0A0"
--subtitle-outline-color "#101010"
```

## 5.3 Video and Audio Backgrounds

For MV/video input, the final video keeps the original frame size. Subtitles are
drawn over the lower part of the image with outline styling; no separate bottom
subtitle band is appended.

For audio-only input, the final video uses:

- the selected **音频背景图**, scaled and cropped to fill the output canvas; or
- a built-in dark default background when no image is selected.

CLI equivalent:

```powershell
--background-image "D:\path\to\cover.png"
```

## 6. Outputs

For one input named `song.mp4`, the output structure is:

```text
song_qwen_fa/
  work/audio/
  alignments/
  subtitles/
  videos/individual/
  batch_plan.json
  batch_manifest.json
```

The user-facing final MP4 is usually:

```text
videos/individual/r2_vocal_windowed.mp4
```

If a stage fails, inspect:

```text
alignment.progress.json
alignment.failure.json
batch_manifest.json
```
