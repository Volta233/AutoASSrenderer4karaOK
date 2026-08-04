# LyricAlignment

中英日多语言歌声的已知歌词强制对齐（Qwen Forced Aligner + realign）研究项目。
仓库根固定为 `LyricAlignment/`；日期后缀只用于 archive/run/report，不进入包名。

## Project
- 目标：将已知歌词与歌声音频对齐，输出字符级 timeline；中文优先，扩展英/日。
- 语言/栈：Python ≥3.10；依赖 numpy、PyYAML、matplotlib、pypinyin（见 `pyproject.toml`），
  可选：huggingface_hub + soundfile（qwen-smoke）、nagisa（demo-multilingual）、demucs（demo-demucs）、pytest（test）。
- 入口：核心实现 `src/lyricalign/`；命令入口全部在 `scripts/`（多为 Python，`.sh` 为批次封装）。
- 运行环境：**必须**用 conda 环境 `lyricalign-qwen`（含 transformers 5.15.0.dev0、torch、
  nagisa、soundfile、numpy、pytest 等全部相关依赖）。项目包尚未 `pip install -e .`，
  当前以 `PYTHONPATH=src` 运行即可（`src/` 为 setuptools package root）。

## Commands
```bash
source /root/miniconda3/etc/profile.d/conda.sh
conda activate lyricalign-qwen
# 若需安装项目包：pip install -e .（在 src/ 内）
PYTHONPATH=src python -m pytest -q   # 全量测试（tests/，288 项）
python scripts/environment/capture_environment.py --out <run>/environment_full.json  # 记录运行环境

# 通用 demo（R2 + vocal + windowed），同名媒体文件和同名 TXT
bash scripts/demo/run_qwen_fa_batch.sh /path/to/media_or_folder

# 后续所有命令均在该 conda 环境下执行；demo 类还需 PYTHONPATH=src

# Inline Realign v4（现行版本），先 smoke 再 formal
bash scripts/demo/verify_inline_realign_v4.sh
RENDER_MODE=skip bash scripts/demo/run_inline_realign_smoke.sh
bash scripts/demo/run_inline_realign_render_only.sh smoke <OUT_ROOT>
RENDER_MODE=skip bash scripts/demo/run_inline_realign_formal.sh
bash scripts/demo/run_inline_realign_render_only.sh formal <OUT_ROOT>
```

## Architecture
- `src/lyricalign/demo/` — 主要工作区：inline_realign、run_state、window_planning、
  media_render、karaoke、visual_diagnostics、alignment_artifacts、gpu_boundary_decoder、batch。
- `src/lyricalign/research_v6/` — detector/repair（E0–E5）与 windowing/decoders/metrics。
- `src/lyricalign/inference/` — Qwen Forced Aligner 薄封装与统一输出 schema。
- `src/lyricalign/metrics/` — 字符指标（canonical `character_interval_metrics_v3_tolerant`）。
- `src/lyricalign/datasets/`、`training/` — M4Singer/MIR-1K 预处理、split、LoRA 训练（qwen_fa_*）。
- `scripts/demo/` — `run_inline_realign_*` 系列可复现入口 + 各种 collect/render/verify 配套脚本。
- `configs/` 模型/训练/数据/metric 配置；`tests/` 回归与执行合同测试。

## Conventions
- 命令入口只在 `scripts/`；核心逻辑进 `src/lyricalign/`。
- 强约束：checkpoint 只许 validation 选择；不得依据 test/OOD 改 checkpoint；
  不静默覆盖原始 aggregate JSON；metric 修正必须从逐字符 reference/prediction 重算。
- `rule_validated` 是 weak supervision，不等于人工 GT。
- Realign 默认 **shadow-only**：`actual_writeback` 必须保持 `0`。
- checkpoint、模型缓存、音频、大型 prediction 文件不进入 Git/archive。
- 普通中断不清理目录；用相同 `OUT_ROOT` + `RESUME=1` 重跑可续。
- 日文解析后词单元直接进 forced-aligner prompt，不二次分词；中英混杂保留连续拉丁词。

## Notes
- （后续在此追加临时事实。）

