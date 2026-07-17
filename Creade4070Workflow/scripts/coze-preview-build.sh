#!/usr/bin/env bash
set -euo pipefail

# 基于脚本位置定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "[coze-preview-build] Working directory: $(pwd)"
echo "[coze-preview-build] Installing dependencies..."

# 检查 uv 是否可用
if command -v uv &> /dev/null; then
    uv sync --frozen
else
    echo "[coze-preview-build] uv not found, skipping dependency installation"
    echo "[coze-preview-build] Dependencies should be pre-installed in preview environment"
fi

echo "[coze-preview-build] Build completed"
