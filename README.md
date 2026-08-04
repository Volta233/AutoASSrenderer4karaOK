# LyricAlignment

中文优先、正在扩展多语言歌声的已知歌词强制对齐研究项目。仓库根目录固定为
`LyricAlignment/`；日期后缀只用于 archive、run 和报告。


Raw + guarded Demucs karaoke demo:

```text
docs/manual/raw_guarded_karaoke_demo.md
```

GUI karaoke rendering tool:

```text
docs/manual/gui_karaoke_tool.md
```

The corresponding detector/repair experiments E0-E5 are launched with:

```bash
bash scripts/demo/run_raw_guarded_experiment_suite.sh
```

## 当前状态（2026-07-26）

Qwen Forced Aligner 首轮 LoRA 实验链已经完成并归档：

- R0 raw；
- matched-budget R1 projector-only；
- R2 projector + top-half audio-attention LoRA；
- M4Singer validation-only checkpoint selection；
- M4Singer sealed test；
- MIR-1K vocal-only OOD；
- approximately 22/33/48/152-second diagnostics；
- second full R2 seed；
- missing-evaluation recovery；
- metric and identity repair。

Primary song-macro penalized boundary MAE:

| Model | M4Singer test | MIR-1K OOD |
|---|---:|---:|
| R0 raw | 251.391 ms | 97.108 ms |
| R1 projector-only | 90.775 ms | 44.007 ms |
| R2 seed3407 | 79.590 ms | 42.557 ms |
| R2 seed20260724 | 80.920 ms | 40.459 ms |

当前不应立即扩展 LoRA；下一步是定位 approximately 150-second 序列中的
局部对齐崩溃。


## 当前 Demo / inline-realign 执行入口（2026-07-28）

当前执行版本为 **Inline Realign v4 full mechanism**。正式运行前：

```bash
cd /home/hyan/LyricAlignment
bash scripts/demo/verify_inline_realign_v4.sh
```

推荐先完成推理、指标、静态诊断和 compact evidence，再单独渲染：

```bash
RENDER_MODE=skip bash scripts/demo/run_inline_realign_smoke.sh
bash scripts/demo/run_inline_realign_render_only.sh smoke \
  /home/hyan/Data/lyricalign/demo_diagnostics/inline_realign_smoke_v4_full_20260728
```

Smoke 通过后运行 formal：

```bash
RENDER_MODE=skip bash scripts/demo/run_inline_realign_formal.sh
bash scripts/demo/run_inline_realign_render_only.sh formal \
  /home/hyan/Data/lyricalign/demo_diagnostics/inline_realign_formal_v4_full_20260728
```

普通中断不清理目录，使用相同 `OUT_ROOT` 和 `RESUME=1` 重跑。Run、stage、experiment item、visual item 和 render item 都具有独立身份与完整性检查。

当前套件包括：

- 30/60 秒固定、静音吸附和严格静音边界；
- 全静音压缩诊断；
- Raw→processor→selected→final 及最小修复；
- stable 音频—歌词同步 exact/−2/−4 裁剪；
- 少给、正确给和多给歌词的文本剂量实验；
- exact/+2/+4、结构不升、零时长宽松和中位融合 realign；
- canonical tolerant GT 指标；
- 中文字符 timeline、完整离散时长 PMF、三层 inconsistency 图；
- 每个 Demo 的窗口、realign、realign 执行、decoder 和 behavior 五类视频。

所有 realign 仍为 shadow-only，`actual_writeback` 必须保持 0。

详见：

```text
docs/sessions/20260728_inline_realign_v4_full_implementation_archive.md
docs/experiments/20260728_inline_realign_full_mechanism_design.md
docs/manual/inline_realign_smoke_formal.md
docs/status/next_execution_plan.md
```

## 通用 Demo 入口

默认对同名媒体和 TXT 执行 `R2 + vocal + windowed`：

```bash
scripts/demo/run_qwen_fa_batch.sh /path/to/media_or_folder
```

- 视频：原画面上方保持不变，字幕在新增的底部黑色区域；
- 音频：纯黑背景；
- 默认最终音轨仍为原始歌曲，`vocal` 仅表示对齐输入；
- 可选 R0/R1/R2、mix/vocal、full/windowed 单独或复合输出；
- `hard_core_forward_overlap_compression_v6` 以歌词单元开始时间决定 60 秒核心归属；
  当前核心内开始的单元完整提交，跨过核心末端也不截断；下一窗只把已提交
  单元作为上下文，新的预测若与冻结前缀重叠，只裁掉左侧重叠部分，可压缩为
  零时长，但不把结束时间整体后移；
- `--language English` 使用英文词级输入；`--language Japanese` 使用 Nagisa 日文词级输入；
- 日文解析后的词单元直接进入 forced-aligner chat template，不再二次分词；
- 对齐失败保留 `alignment.progress.json` 与 `alignment.failure.json`，render
  会跳过该项而不是追加一个缺失 `alignment.json` 的次生错误；
- 中文/粤语按汉字字符并保留连续拉丁词，中英混杂不会再拆成英文字母；
- 详细说明见 `docs/manual/qwen_fa_batch_demo.md`；
- 实验讨论、失败实现与证据链见 `docs/sessions/20260726_demo_exploration_archive.md`。

## 必读入口

1. `AI_SESSION_ENTRY.md`
2. `docs/status/project_current.md`
3. `docs/sessions/20260726_demo_exploration_archive.md`
4. `docs/manual/qwen_fa_batch_demo.md`
5. `docs/sessions/20260724_qwen_fa_followup_repair_archive.md`
6. `reports/review/20260724_qwen_fa_followup_repair_review.md`
7. `reports/audits/20260724_qwen_fa_long_b180_outlier_audit.md`
8. `docs/status/next_execution_plan.md`

## 指标口径

Canonical schema:

```text
character_interval_metrics_v3_tolerant
```

关键规则：

- valid / invalid / missing 互斥；
- 主指标为带缺失惩罚的 per-song macro boundary MAE；
- valid-only 使用完全相同的有效集合计算分子与分母；
- `character_coverage` 为有效字符比例；
- `song_coverage` 为至少有一个有效字符的歌曲比例；
- `complete_song_coverage` 为整首全部字符有效的歌曲比例。

原始 v2 指标不覆盖；修正版位于：

```text
results/recomputed/20260724_character_metrics_v3/
```

## Canonical result files

```text
results/comparisons/20260724_qwen_fa_followup_final_summary.json
reports/progress/20260724_qwen_fa_overnight_overall_summary.md
reports/audits/20260724_qwen_fa_long_b180_outlier_audit.md
```

旧的 provisional summary 保留作为历史证据，不作为当前正式结果表。

## 强约束

- checkpoint 只允许使用 validation 选择；
- 不根据 test/OOD 修改 checkpoint；
- 不静默覆盖原始 aggregate JSON；
- metric 修正必须从逐字符 reference/prediction 重算；
- `rule_validated` 是 weak supervision，不等同于人工 GT；
- synthetic approximately 152.5-second set 不是自然三分钟 benchmark；
- checkpoint、模型缓存、音频和大型 prediction 文件不进入 Git/archive。

## 顶层目录

| 路径 | 作用 |
|---|---|
| `configs/` | 模型、训练、数据和 metric 配置 |
| `data/` | 轻量 schema、registry 和 manifest 模板 |
| `src/lyricalign/` | 核心实现 |
| `scripts/` | 可复现入口 |
| `runs/` | 轻量执行证据与外部产物索引 |
| `results/` | 原始、修正和比较结果 |
| `reports/` | 审计、进展和研究报告 |
| `docs/` | 原则、状态、manual 和 session |
| `tests/` | 回归与执行合同测试 |

Remote:

```text
git@github.com:heyan2454/LyricAlignment.git
```
