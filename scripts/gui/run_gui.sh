#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

GUI_PYTHON="${GUI_PYTHON:-D:/Anconda/envs/qwen312/python.exe}"
PIPELINE_PYTHON="${LYRICALIGN_PIPELINE_PYTHON:-$GUI_PYTHON}"

export LYRICALIGN_PIPELINE_PYTHON="$PIPELINE_PYTHON"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

cd "$ROOT"
exec "$GUI_PYTHON" "$ROOT/scripts/gui/run_gui.py"
