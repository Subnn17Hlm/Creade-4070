#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

echo "[coze-preview-build] Working directory: $(pwd)"

if ! command -v uv >/dev/null 2>&1; then
    echo "[coze-preview-build] ERROR: uv not found"
    exit 1
fi

uv sync --frozen --no-dev

python3 -c "
import fastapi
import uvicorn
import langgraph
print('[coze-preview-build] Core modules OK')
"
echo "[coze-preview-build] Build completed"
