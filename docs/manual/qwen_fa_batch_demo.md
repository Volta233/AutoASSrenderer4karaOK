# Qwen Forced Aligner Batch Demo

`scripts/demo/run_qwen_fa_batch.py` 是 GUI 背后的批处理入口。它适合批量处理文件夹、手动指定模型/checkpoint、只重跑某个阶段，或在无 GUI 环境中生成成品视频。

普通用户优先使用：

```text
docs/manual/gui_karaoke_tool.md
```

## 1. 基本命令

PowerShell 示例：

```powershell
cd D:\小玩具\纯K工具v004\LyricAlignment
$env:PYTHONPATH = "src"
D:\Anconda\envs\qwen312\python.exe scripts\demo\run_qwen_fa_batch.py `
  "D:\小玩具\纯K工具v003\LocalInputs\夜苏打\夜苏打.mp4" `
  --lyrics "D:\小玩具\纯K工具v003\LocalInputs\夜苏打\歌词.txt" `
  --language Chinese `
  --preset default `
  --separator demucs `
  --demucs-device cpu `
  --render-audio source `
  --output-dir "D:\小玩具\纯K工具v003\LocalOutputs\夜苏打"
```

默认 `preset default` 生成：

```text
R2 + separated vocal + windowed alignment
```

最终视频通常在：

```text
<输出目录>/<歌曲名>/videos/individual/r2_vocal_windowed.mp4
```

## 2. 输入规则

支持视频：

```text
.mp4 .mkv .mov .webm .avi .m4v
```

支持音频：

```text
.wav .flac .mp3 .m4a .aac .ogg .opus .wma
```

可以处理单个媒体，也可以处理目录。同名发现规则仍可用：

```text
歌曲A.mp4
歌曲A.wav
歌曲A.txt
```

如果歌词文件不和媒体同名，使用：

```powershell
--lyrics "D:\path\to\lyrics.txt"
```

## 3. 阶段与续跑

完整流程：

```powershell
--stage all
```

单独阶段：

```powershell
--stage prepare
--stage align
--stage render
```

强制刷新：

```powershell
--force-prepare
--force-separation
--force-align
--force-render
```

只修改字体、颜色、背景图或字幕高度时，通常只需：

```powershell
--stage render --force-render
```

## 4. 模型与 R2 checkpoint

推荐显式使用本地路径：

```powershell
--model "D:\小玩具\纯K工具v004\LyricAlignment\models\hf\Qwen3-ForcedAligner-0.6B-hf" `
--r2-checkpoint "D:\小玩具\纯K工具v004\LyricAlignment\models\R2checkpoint"
```

R2 checkpoint 目录结构：

```text
models/R2checkpoint/
  adapter/
  checkpoint_identity.json
  projector.pt
  trainer_state.pt
```

`projector.pt` 必须直接在 checkpoint 根目录下。R2 是中文歌唱数据微调 checkpoint；非中文语言可以运行，但应把结果视为未正式验证。

如果日志出现：

```text
model type `qwen3_asr`
```

请确认当前 Python 环境使用支持 Qwen Forced Aligner 的 Transformers 源码版。

## 5. Demucs

GUI 默认会传 `--demucs-device cpu`，适合本机稳定运行。CLI 裸跑时建议也显式指定设备：

```text
--separator demucs
--demucs-version 4.1.0
--demucs-model htdemucs_ft
--demucs-device cpu
```

推荐让 Demucs 安装在同一个 pipeline Python 环境里。若使用独立环境，可指定：

```powershell
--demucs-command "conda run -n demucs demucs"
```

如果之前的配置指向不存在的：

```text
D:\Anconda\envs\demucs
```

会触发 `EnvironmentLocationNotFound`。解决方式是安装/使用同一个 `qwen312` 环境里的 Demucs，或把 `--demucs-command` 改为真实存在的环境。

## 6. 字幕渲染

当前成品视频使用嵌入式覆盖字幕：

- 视频输入：不改变原视频画布尺寸，字幕覆盖在下半区。
- 音频输入：输出固定画布，字幕同样位于下半区。
- 不再生成额外底部黑条。
- 不再烧录左上角语言/模型/分离器注释。

手动设置字幕高度：

```powershell
--subtitle-band-height 260
```

颜色支持 `#RRGGBB`、`RRGGBB`、`&HBBGGRR`、`&HAABBGGRR`：

```powershell
--karaoke-primary-color "#FFE800" `
--karaoke-secondary-color "#909090" `
--subtitle-preview-color "#A0A0A0" `
--subtitle-outline-color "#101010"
```

## 7. 音频背景图

音频输入没有原视频画面。默认使用深色背景：

```powershell
--audio-width 1280 --audio-height 720
```

指定背景图：

```powershell
--background-image "D:\path\to\cover.png"
```

背景图会等比放大铺满画布并居中裁切。字幕仍在下半区。

## 8. 字体

中文默认优先尝试：

```text
Noto Sans CJK SC
```

Windows 没有该字体时，渲染器会尝试常见中文字体回退。也可以直接指定：

```powershell
--font "Microsoft YaHei"
```

## 9. 输出结构

单首歌输出目录：

```text
<输出目录>/<歌曲名>/
  work/audio/
  alignments/
  subtitles/
  videos/individual/
  batch_plan.json
  batch_manifest.json
```

关键文件：

```text
alignments/r2/vocal/windowed/alignment.json
subtitles/r2_vocal_windowed.ass
videos/individual/r2_vocal_windowed.mp4
```

失败时查看：

```text
alignment.progress.json
alignment.failure.json
batch_manifest.json
```

## 10. 常用排错

`missing audio file: ... mix.wav`：

当前版本会在准备阶段发现 mix 缺失时重建。重新运行同一命令即可；如果只想强制准备：

```powershell
--stage prepare --force-prepare
```

字幕仍是旧位置或仍有左上角注释：

```powershell
--stage render --force-render
```

字体缺失：

```powershell
--font "Microsoft YaHei"
```

Demucs conda 环境不存在：

省略 `--demucs-command`，让脚本优先尝试当前 Python 环境中的 Demucs；或者填写真实存在的 `conda run -n <env> demucs`。
