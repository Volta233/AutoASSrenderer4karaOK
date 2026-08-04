# 通用 Qwen Forced Aligner 批量 Demo

## 1. 默认用途

入口：

```bash
scripts/demo/run_qwen_fa_batch.sh
```

默认执行：

```text
R2 + 分离人声输入 + hard_core_forward_overlap_compression_v6 串行分窗
```

默认只生成这一组 alignment 和视频，不会像历史“夜苏打”入口一样自动跑
12 组矩阵。

## 2. 输入命名

同一首歌使用相同文件名主体：

```text
歌曲A.mp4
歌曲A.wav       # 可选的高质量同名音频
歌曲A.txt
```

支持视频：

```text
.mp4 .mkv .mov .webm .avi .m4v
```

支持音频：

```text
.wav .flac .mp3 .m4a .aac .ogg .opus .wma
```

规则：

- 必须有 `<文件名>.txt`；
- 至少有一个同名视频或音频；
- 有视频时，视频作为画面；
- 同时有音频时，音频优先作为对齐和 Spleeter 输入；
- 没有独立音频时，从视频提取音轨；
- 文件夹模式会忽略缺少 TXT 或媒体的不完整组。

## 3. 最常用命令

处理单个视频：

```bash
cd /home/hyan/LyricAlignment
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A.mp4
```

也可只输入 TXT、无扩展名主体或目录：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A.txt
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A
scripts/demo/run_qwen_fa_batch.sh /path/to/folder
```

递归处理目录：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/folder --recursive
```

目录中只处理指定主体：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/folder --name 歌曲A
```

## 3.1 失败诊断文件

每个 alignment 目录现在最多包含三类 JSON：

```text
alignment.json           # 仅成功时存在
alignment.progress.json  # 执行中及失败后保留的最近窗口/尝试状态
alignment.failure.json   # 失败类型、traceback、结构化边界诊断与请求 identity
```

重新执行不同请求时会先移除旧 `alignment.json`，避免失败后误用旧结果。
若对齐失败，render 会记录 `alignment_failed_or_missing` 并跳过，不再追加一个
掩盖原始原因的 `FileNotFoundError`。

窗口策略 `hard_core_forward_overlap_compression_v6` 始终保留 nominal 左音频
重叠。每个 60 秒核心完全采用当前窗预测：歌词单元只要开始时间早于核心末端，
就由当前窗完整提交，即使结束时间跨过核心末端。下一窗重新输入的已提交歌词
只作为上下文，不覆盖旧结果。

对下一窗的首批未提交单元，不再要求其预测开始时间必须位于核心起点之后。合并
时以上一已提交单元的最终结束时间作为下限，逐项执行：

```text
final_start = max(current_window_start, previous_final_end)
final_end   = max(current_window_end, final_start)
```

因此只裁掉左侧重叠，不为保持时长而整体后移。完全落在冻结边界前的单元可以
压缩成零时长。`selected_start_sec` / `selected_end_sec` 保留当前窗原始预测，
`overlap_compression_*` 字段记录压缩量、下限和是否归零。旧的 core-start 拒绝、
去掉左重叠重跑以及上一窗 lookahead 替换均不再使用。

## 4. 指定语种与对齐单位

默认语种仍为中文：

```bash
--language Chinese
```

当前批处理入口可直接指定 Qwen3 Forced Aligner 的官方语种名称，常用示例：

```bash
# 英文歌词：按词对齐
scripts/demo/run_qwen_fa_batch.sh /path/to/english_song.mp4 \
  --language English

# 日文歌词：使用 Nagisa 分词后按词对齐
scripts/demo/run_qwen_fa_batch.sh /path/to/japanese_song.mp4 \
  --language Japanese

# 粤语歌词：汉字仍按字符对齐，但语言条件改为 Cantonese
scripts/demo/run_qwen_fa_batch.sh /path/to/cantonese_song.mp4 \
  --language Cantonese
```

也接受常用别名：

```text
zh / 中文       -> Chinese
en / 英文       -> English
ja / 日文 / 日语 -> Japanese
yue / 粤语      -> Cantonese
```

当前入口完整允许值：

```text
Chinese, English, Cantonese, French, German, Italian,
Japanese, Portuguese, Russian, Spanish
```

官方模型还支持 Korean，但官方处理器对韩文使用独立的 `soynlp` 词典分词。
当前补丁没有复制该词典资产，因此 CLI 会拒绝 Korean，而不是用错误的空格分词
静默运行。

### 4.1 文本如何切成对齐单元

不同语种不再统一按 Unicode 字符拆分：

```text
Chinese / Cantonese:
  今晚 sing with me
  -> 今 | 晚 | sing | with | me

English:
  Don't stop now.
  -> Don't | stop | now

Japanese:
  今日は、晴れです。
  -> 由 Nagisa 得到日文词单元；标点只用于显示，不单独占时间戳
```

输出 JSON 为兼容旧脚本仍保留 `characters`、`character_count` 等字段名，
但每一项现在代表一个 **alignment unit**，可能是汉字，也可能是完整英文词或日文词。
新增字段包括：

```text
identity.language
identity.alignment_unit_mode
summary.alignment_unit_count
characters[].alignment_unit
characters[].unit_type
characters[].display_text
characters[].display_prefix
characters[].display_suffix
```

显示用标点和空格会被保留在 `display_*` 中；传给模型的文本只包含官方
processor 接受的对齐单元。因此英文不会被错误拆成单个字母，日文标点也不会
额外产生 timestamp slot。

历史参数名 `--minimum-forward-characters` 和 JSON 中部分 `character_*` 字段为
兼容旧结果暂未改名；在 English/Japanese 模式下，它们实际表示 alignment unit
数量，而不是 Unicode 字符数。

### 4.2 日文运行依赖

日文分词与 Qwen 官方实现一致，依赖 `nagisa`。服务器的 Qwen3-ASR 环境若未
包含它，可安装项目可选依赖：

```bash
cd /home/hyan/LyricAlignment
pip install -e '.[demo-multilingual]'
```

或仅安装：

```bash
pip install nagisa
```

缺少依赖时会在加载模型前给出明确错误，不会退化成逐字符日文切分。

### 4.3 批量目录的语种口径

一次命令中的所有同名文件组共享同一个 `--language`。不同语种文件建议分开
执行并指定不同输出目录。例如：

```bash
scripts/demo/run_qwen_fa_batch.sh /data/en --language English
scripts/demo/run_qwen_fa_batch.sh /data/ja --language Japanese
```

切换 `--language` 会改变 alignment request hash，并自动使旧对齐缓存失效。
`lyrics_structure.json` 也会重写，避免中文字符结构与英文/日文词级结果混用。

### 4.4 模型选择提醒

基础 R0 Forced Aligner 是官方多语种模型；当前 R2 是基于中文歌唱数据微调的
LoRA。批处理仍按既定需求默认使用 R2，但 English/Japanese/Cantonese 下会输出
`r2_multilingual_not_validated` 警告。需要判断跨语种能力时，建议同时生成 R0：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/song \
  --language English \
  --individual r0:vocal:windowed \
  --individual r2:vocal:windowed
```

这只是公平对照入口；当前没有英文或日文歌唱 GT，不能仅凭视频观感认定 R2
优于或劣于 R0。

### 4.5 字体默认值

未指定 `--font` 时会按语种选择：

```text
Japanese            -> Noto Sans CJK JP
Chinese / Cantonese -> Noto Sans CJK SC
其他语种            -> Noto Sans
```

仍可手动覆盖：

```bash
--font 'Source Han Sans JP'
```

Windows 本机没有 `Noto Sans CJK SC` 时，不再因为缺少该字体直接中断。渲染会
尝试常见 CJK 字体并输出 `subtitle_font_fallback` 日志。中文 Windows 可直接用：

```bash
--font "Microsoft YaHei"
```

## 5. 输出位置

默认在源文件同一目录建立：

```text
歌曲A_qwen_fa/
├── work/audio/
├── alignments/
├── subtitles/
├── videos/
├── batch_plan.json
└── batch_manifest.json
```

指定统一输出根目录：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/folder \
  --output-dir /home/hyan/Data/lyricalign/demo_outputs
```

输出会写入：

```text
/home/hyan/Data/lyricalign/demo_outputs/歌曲A/
/home/hyan/Data/lyricalign/demo_outputs/歌曲B/
```

## 6. 视频与音频渲染

### 视频输入

输出不再向下扩展黑色字幕带。字幕会覆盖在原视频下半部分区域：

```text
原视频画面
  下半部分覆盖两行描边 KTV 字幕
```

输出视频保持原始画布尺寸。字幕区域默认约为画面高度的三分之一；可指定：

```bash
--subtitle-band-height 260
```

字幕样式按 ASS/Aegisub 的颜色模型写入 `[V4+ Styles]`。CLI 接受 `#RRGGBB`、
`RRGGBB`、`&HBBGGRR` 或 `&HAABBGGRR`：

```bash
--karaoke-primary-color "#FFE800" \
--karaoke-secondary-color "#909090" \
--subtitle-preview-color "#A0A0A0" \
--subtitle-outline-color "#101010"
```

### 音频输入

没有视频时生成纯黑背景，默认 1280×720：

```bash
--audio-width 1280 --audio-height 720
```

也可为音频输入指定背景图；图片会等比铺满画布并裁切：

```bash
--background-image /path/to/cover.png
```

未指定背景图时使用内置深色背景，不再是纯黑屏。

### 输出音轨

默认：

```bash
--render-audio source
```

即使对齐使用分离人声，最终视频仍播放原始歌曲音轨。用于诊断时可改为：

```bash
--render-audio aligned
```

此时 `vocal` 结果播放分离人声，`mix` 结果播放原始混音。

## 7. 其他单独输出

格式：

```text
MODEL:AUDIO:MODE
```

可选值：

```text
MODEL = r0 | r1 | r2
AUDIO = mix | vocal
MODE  = full | windowed
```

示例：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A \
  --individual r1:mix:full
```

同时生成多组：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A \
  --individual r2:vocal:windowed \
  --individual r2:mix:windowed
```

一旦显式指定输出模式，不会再隐式加入默认模式。

## 8. 复合视频

### R0/R1/R2 三模型比较

默认比较 vocal/windowed：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A \
  --preset compare-models
```

指定其他输入和模式：

```bash
--compare-models mix:full
--compare-models vocal:windowed
```

依赖的三个单独视频会自动生成。

### 同模型四种输入比较

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A \
  --compare-inputs r2
```

四个面板为：

```text
mix/full       mix/windowed
vocal/full     vocal/windowed
```

### 预设

```text
default         R2 vocal windowed
all-individual  12 个单独视频
compare-models  R0/R1/R2 vocal windowed 三联
compare-inputs  R2 四输入四联
full-demo       历史 12 单独 + 4 三联 + 3 四联
```

例如：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/歌曲A --preset full-demo
```

## 9. 分阶段与续跑

准备音频和 Spleeter：

```bash
--stage prepare
```

只对齐：

```bash
--stage align
```

只渲染：

```bash
--stage render
```

全部：

```bash
--stage all
```

缓存由输入文件、模型/checkpoint、歌词、语种、对齐单元、分窗参数和渲染参数共同决定。
中断后重新执行同一命令即可续跑。

按阶段强制重跑：

```bash
--force-prepare
--force-separation
--force-align
--force-render
```

全部强制：

```bash
--force
```

## 10. Spleeter

默认模型目录：

```text
~/.cache/spleeter_models
```

推荐服务器目录：

```bash
export SPLEETER_MODEL_ROOT=/root/autodl-tmp/AST_storage/Data/lyricalign/models/spleeter
```

显式保存的权重可直接使用，不要求目录中存在 `.probe`。运行时会检查实际模型文件：

- TensorFlow checkpoint：同一前缀的 `*.index` 与 `*.data-*`；
- 或 SavedModel：`saved_model.pb` 与 `variables/variables.*`。

可以传模型根目录：

```bash
--spleeter-model-root /root/.cache/spleeter_models
```

也可以直接传 `2stems` 模型目录：

```bash
--spleeter-model-root /path/to/spleeter_models/2stems
```

独立检查现有权重：

```bash
python scripts/demo/validate_spleeter_model.py \
  --model-root /root/.cache/spleeter_models
```

输出中的 `marker_present` 只说明 `.probe` 是否存在，不决定模型是否可用。
旧版本因缺少 `.probe` 报错时，可以临时执行：

```bash
touch /root/.cache/spleeter_models/2stems/.probe
```

但推荐应用新实现，让程序验证实际权重，而不是仅信任 marker。

首次下载或损坏后重建：

```bash
bash scripts/demo/download_spleeter_model_resumable.sh
```

默认查找 PATH 中的 `spleeter`，否则尝试：

```text
conda run -n spleeter spleeter
```

可覆盖：

```bash
--spleeter-env my_env
--spleeter-command 'conda run -n my_env spleeter'
```

人声输出必须通过静音、近似复制和双 stem 一致性检查，否则批次失败。

## 11. 模型与 checkpoint

默认沿用服务器已归档路径。也可显式设置：

```bash
--model /path/to/Qwen3-ForcedAligner-0.6B-hf
--revision <revision>
--r2-checkpoint /path/to/r2/checkpoint
--r1-checkpoint /path/to/r1/checkpoint
```

若未显式给 checkpoint，将从相应 run 的 `best_checkpoint.json` 解析，只接受
validation 选择结果。

## 12. 运行前预览

不会解码媒体、运行 Spleeter、加载模型或写结果：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/folder --dry-run
```

它会打印发现的同名组、输出目录和完整模式计划。

## 13. 失败与日志

每首歌保存：

```text
batch_plan.json
batch_manifest.json
```

manifest 包含输入、模式、视频输出和失败信息。文件夹批处理默认继续处理后续歌曲，
但只要任一歌曲失败，最终返回码为非零。需要首错停止时使用：

```bash
--fail-fast
```

## 14. 研究边界

- Demo 不含正式 GT metric；
- 不根据主观视频选择 checkpoint；
- `full` 和 `windowed` 不应混为同一指标口径；
- `vocal` 是对齐输入，不代表最终视频必须播放 vocal；
- 当前分窗是 60 秒核心、左右各 10 秒上下文；
- 对缺词、多词、错词的自动恢复仍未正式实现；
- English/Japanese/Cantonese 目前仅完成接口与可复现映射支持，尚无歌唱 GT 指标；
- 中英或日英混杂可按主要演唱语言指定，但混合语言条件的效果需要另行验证。
