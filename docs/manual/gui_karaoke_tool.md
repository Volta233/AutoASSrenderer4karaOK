# GUI Karaoke Rendering Tool

本手册说明如何用图形界面把一首歌生成带 KTV 高亮字幕的 MP4。GUI 是 `scripts/demo/run_qwen_fa_batch.py` 的封装，适合日常使用；需要批量或精细调参时再使用 CLI。

## 1. 启动 GUI

推荐从 Git Bash 启动：

```bash
cd /d/小玩具/纯K工具v004/LyricAlignment
bash scripts/gui/run_gui.sh
```

也可以从 PowerShell 启动：

```powershell
cd D:\小玩具\纯K工具v004\LyricAlignment
$env:PYTHONPATH = "src"
D:\Anconda\envs\qwen312\python.exe scripts\gui\run_gui.py
```

`scripts/gui/run_gui.sh` 默认使用：

```text
GUI_PYTHON=D:/Anconda/envs/qwen312/python.exe
LYRICALIGN_PIPELINE_PYTHON=D:/Anconda/envs/qwen312/python.exe
```

需要换环境时：

```bash
GUI_PYTHON="D:/path/to/python.exe" \
LYRICALIGN_PIPELINE_PYTHON="D:/path/to/python.exe" \
bash scripts/gui/run_gui.sh
```

GUI 可以由任何带 Tkinter 的 Python 打开，但实际生成任务使用 **Python 解释器** 输入框里的 Python。这个环境需要能加载 Qwen Forced Aligner、Demucs、FFmpeg 相关依赖。

## 2. 最短可用流程

1. 在 **音频 / MV** 中选择 `.mp4`、`.wav`、`.mp3` 等媒体。
2. 在 **歌词 TXT** 中选择歌词文件；也可以直接把歌词粘贴到文本框。
3. 选择 **输出目录**。
4. 保持默认：
   - 语言：`Chinese`
   - 阶段：`all`
   - 预设：`default`
   - 分离器：`demucs`
   - 成品音轨：`source`
5. 点击 **预览 dry-run**，确认日志中的输入、输出和命令。
6. 点击 **开始生成成品**。

最终视频通常在：

```text
<输出目录>/<歌曲名>/videos/individual/r2_vocal_windowed.mp4
```

如果失败，先看 GUI 日志末尾的 JSON；再检查：

```text
batch_manifest.json
alignment.failure.json
alignment.progress.json
```

## 3. 输入与歌词

支持常见视频：

```text
.mp4 .mkv .mov .webm .avi .m4v
```

支持常见音频：

```text
.wav .flac .mp3 .m4a .aac .ogg .opus .wma
```

歌词 TXT 优先级高于粘贴文本。只粘贴歌词时，GUI 会临时生成一个 TXT 文件传给批处理脚本。

## 4. 字幕和画面

当前渲染目标是“字幕嵌入原画面”，不是把字幕和视频画布分离。

视频/MV 输入：

- 输出保持原始画布尺寸。
- 字幕覆盖在原视频下半区。
- 不再新增底部黑色字幕带。

音频输入：

- 默认输出 1280x720 深色背景视频。
- 如果选择 **音频背景图**，图片会等比铺满画布并裁切。
- 高亮行和未唱行都在画面下半区，不会一行上半屏、一行下半屏。

**字幕带高度** 可以留空。留空时使用自动高度，约为画面高度的三分之一。只有需要手动微调字幕占用范围时才填写像素值。

## 5. 颜色与字体

GUI 中的 **字幕颜色** 是调色盘按钮：

- 高亮：当前唱到的文字。
- 未唱：当前行中尚未唱到的文字。
- 预览：即将唱到的下一行。
- 描边：字幕描边颜色。

默认颜色：

```text
高亮: #FFE800
未唱: #909090
预览: #A0A0A0
描边: #101010
```

字体可以留空，程序会按语言自动选择。Windows 本机没有 `Noto Sans CJK SC` 时，可以在 **字幕字体** 中填写：

```text
Microsoft YaHei
```

渲染器会尝试常见 CJK 字体回退，不会仅因缺少 `Noto Sans CJK SC` 直接中断。

成品字幕中不会再烧录左上角的语言、模型、分离器说明文字。

## 6. 模型路径

GUI 默认 Qwen 模型路径：

```text
models/hf/Qwen3-ForcedAligner-0.6B-hf
```

GUI 默认 R2 checkpoint 路径：

```text
models/R2checkpoint
```

R2 checkpoint 目录需要包含：

```text
models/R2checkpoint/
  adapter/
  checkpoint_identity.json
  projector.pt
  trainer_state.pt
```

`projector.pt` 必须直接位于 `models/R2checkpoint` 下。只有在你确认另一个目录结构一致时，才在 GUI 中改 R2 checkpoint。

## 7. Demucs 设置

默认设置：

```text
separator: demucs
demucs version: 4.1.0
model: htdemucs_ft
device: cpu
```

本机推荐把 Demucs 安装在 GUI 使用的同一个 `qwen312` 环境中。这样不需要单独的 `D:\Anconda\envs\demucs` 环境，也不会遇到：

```text
EnvironmentLocationNotFound: Not a conda environment: D:\Anconda\envs\demucs
```

如果你确实使用独立 Demucs 环境，在 **Demucs 命令** 中填写：

```text
conda run -n demucs demucs
```

如果显卡可用，可以把 **对齐设备** 或 **Demucs 设备** 改为 `cuda`；否则保持 `cpu`。

## 8. 续跑与强制刷新

同一输入、同一参数重复运行会复用缓存。需要局部重跑时勾选：

- 重做准备：重新提取/准备音频。
- 重做人声分离：重新运行 Demucs/Spleeter。
- 重做对齐：重新生成时间戳 JSON。
- 重做渲染：重新生成 ASS 和 MP4。

只改字幕样式、背景图或颜色时，通常只需要勾选 **重做渲染**。

## 9. 常见问题

`qwen3_asr` 不被 Transformers 识别：

```text
ValueError: ... model type `qwen3_asr` ...
```

说明当前 Python 环境里的 Transformers 太旧或不是源码版。请在 GUI 选择的同一个 Python 环境中安装支持 Qwen Forced Aligner 的 Transformers。

`missing audio file: ... work\audio\mix.wav`：

重新运行同一任务即可。当前准备阶段会在发现 mix 缺失时从原媒体重建，并复用已有分离结果。

生成结果不是最新样式：

勾选 **重做渲染** 后再运行。当前渲染缓存版本已经更新，正常情况下旧的带左上角注释视频会自动失效并重渲染。
