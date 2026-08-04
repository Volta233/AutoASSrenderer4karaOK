# LyricAlignment

LyricAlignment 是一个面向歌曲字幕烧录的工具链：输入音频或 MV、歌词 TXT 和 Qwen Forced Aligner/R2 checkpoint，自动完成歌词时间轴生成，并输出带 KTV 高亮字幕的 MP4。

当前最实用的入口是 GUI。研究实验、评测和 inline-realign 仍保留在仓库内，但普通成品生成优先使用 `scripts/gui/run_gui.sh` 或 `scripts/gui/run_gui.py`。

## 快速开始

Windows 本机推荐使用 Git Bash 运行：

```bash
cd /d/小玩具/纯K工具v004/LyricAlignment
bash scripts/gui/run_gui.sh
```

也可以直接用 Python 启动：

```powershell
cd D:\小玩具\纯K工具v004\LyricAlignment
$env:PYTHONPATH = "src"
D:\Anconda\envs\qwen312\python.exe scripts\gui\run_gui.py
```

默认 GUI 启动脚本会使用：

```text
D:\Anconda\envs\qwen312\python.exe
```

如果你的 Python 环境不在这个位置，可以覆盖：

```bash
GUI_PYTHON="D:/Anconda/envs/qwen312/python.exe" \
LYRICALIGN_PIPELINE_PYTHON="D:/Anconda/envs/qwen312/python.exe" \
bash scripts/gui/run_gui.sh
```

## GUI 常用流程

1. 选择 **音频 / MV**。
2. 选择歌词 TXT，或把歌词粘贴到文本框。
3. 选择输出目录。
4. 保持默认预设 `default`：`R2 + vocal + windowed`。
5. 首次建议点 **预览 dry-run**，确认命令和路径。
6. 点击 **开始生成成品**。
7. 成品通常位于：

```text
<输出目录>/<歌曲名>/videos/individual/r2_vocal_windowed.mp4
```

GUI 详细说明见：

```text
docs/manual/gui_karaoke_tool.md
```

## 当前成品渲染行为

- MV/视频输入：保持原视频画布尺寸，字幕直接覆盖在画面下半区，不再额外拼接底部黑条。
- 音频输入：默认生成深色背景视频；如果在 GUI 中选择背景图，则使用该图片铺满画布。
- 字幕布局：高亮行和未唱行都位于下半区，模拟常见 KTV/MV 字幕效果。
- 字幕样式：使用 ASS/libass 渲染，支持 Aegisub 风格的描边与 `\kf` 高亮计时。
- 颜色设置：GUI 提供调色盘按钮，不需要用户手写十六进制颜色。
- 元信息标签：成品视频不再烧录左上角的 `Chinese · R2 · ...` 这类说明文字。

## 模型与 checkpoint

默认模型路径：

```text
models/hf/Qwen3-ForcedAligner-0.6B-hf
```

默认 R2 checkpoint 路径：

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

如果 GUI 日志提示 `qwen3_asr` 不被 Transformers 识别，说明当前 Python 环境里的 Transformers 版本不兼容。需要在 GUI 选择的同一个 Python 环境里安装支持 Qwen Forced Aligner 的源码版 Transformers。

## 字体与 Demucs

中文默认字体优先尝试 `Noto Sans CJK SC`。Windows 本机没有该字体时，渲染器会尝试常见中文字体回退；GUI 里也可以直接填写：

```text
Microsoft YaHei
```

默认人声分离器为 Demucs：

```text
demucs==4.1.0
model: htdemucs_ft
mode: --two-stems vocals
```

本机推荐让 Demucs 安装在同一个 `qwen312` 环境中，避免 `EnvironmentLocationNotFound: ...\envs\demucs` 这类独立 conda 环境路径错误。确实使用独立环境时，在 GUI 的 **Demucs 命令** 中填写完整命令，例如：

```text
conda run -n demucs demucs
```

## CLI 入口

GUI 底层调用同一个批处理入口：

```powershell
$env:PYTHONPATH = "src"
python scripts\demo\run_qwen_fa_batch.py "D:\path\to\song.mp4" `
  --lyrics "D:\path\to\lyrics.txt" `
  --preset default `
  --separator demucs `
  --demucs-device cpu `
  --render-audio source `
  --output-dir "D:\path\to\outputs"
```

CLI 详细说明见：

```text
docs/manual/qwen_fa_batch_demo.md
```

## 目录结构

| 路径 | 作用 |
|---|---|
| `src/lyricalign/` | 核心实现、GUI 命令封装、字幕渲染与对齐逻辑 |
| `scripts/gui/` | GUI 启动脚本 |
| `scripts/demo/` | 批处理、渲染、模型实验入口 |
| `docs/manual/` | 面向使用者的手册 |
| `models/` | 本地模型和 checkpoint 放置位置，不应提交大文件 |
| `tests/` | 回归测试 |

## 开发验证

常用 focused 检查：

```powershell
cd D:\小玩具\纯K工具v004\LyricAlignment
$env:PYTHONPATH = "src"
python -m pytest -q tests/test_gui_command.py tests/test_qwen_fa_batch_demo.py
python -m compileall -q src scripts tests
```

仓库仍包含 Qwen Forced Aligner、R0/R1/R2、inline-realign 和评测相关研究资料。研究入口请参考 `AGENTS.md`、`docs/status/` 和 `docs/sessions/`；普通用户生成成品视频优先阅读本 README 与 GUI 手册。
