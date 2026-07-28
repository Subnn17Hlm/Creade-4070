#!/usr/bin/env bash
set -euo pipefail

# 基于脚本位置定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH:-}"

# Use platform-provided PORT, default to 5000
PORT="${PORT:-5000}"

echo "[coze-preview-run] Working directory: $(pwd)"
echo "[coze-preview-run] Starting full workflow preview on port ${PORT}"

exec python3 src/main.py -m http -p "$PORT"
