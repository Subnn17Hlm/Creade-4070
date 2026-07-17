#!/bin/bash
set -eo pipefail

# 根据脚本位置计算项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
echo "[setup] Working directory: $(pwd)"

# 初始化目录
if [ "$COZE_PROJECT_ENV" = "DEV" ]; then
  if [ ! -d "assets" ]; then
    mkdir -p "assets"
  fi
fi

# uv 安装依赖
if [ -n "$PIP_TARGET" ]; then
  echo "[setup] Deploy mode: installing to PIP_TARGET"
  
  # 创建 PIP_TARGET 目录
  if ! mkdir -p "$PIP_TARGET"; then
    echo "[setup] ERROR: Failed to create PIP_TARGET directory"
    exit 1
  fi
  
  # 导出依赖并安装
  echo "[setup] Step 1: Exporting dependencies..."
  if ! uv export --frozen --no-hashes --no-dev > /tmp/requirements_export.txt; then
    echo "[setup] ERROR: uv export failed (exit code: $?)"
    exit 1
  fi
  echo "[setup] Exported $(wc -l < /tmp/requirements_export.txt) dependencies"
  
  echo "[setup] Step 2: Installing to target directory..."
  if ! uv pip install --no-cache --target "$PIP_TARGET" -r /tmp/requirements_export.txt; then
    echo "[setup] ERROR: uv pip install failed (exit code: $?)"
    exit 1
  fi
  
  # 清理临时文件
  rm -f /tmp/requirements_export.txt
  
  # 验证关键模块可导入
  echo "[setup] Step 3: Verifying installation..."
  export PYTHONPATH="$PIP_TARGET:${PYTHONPATH:-}"
  
  # 忽略第三方SDK的SyntaxWarning（如tos/utils.py的invalid escape sequence）
  # 只检查Python退出码，不以stderr内容为准
  export PYTHONWARNINGS="ignore::SyntaxWarning"
  
  VERIFY_RESULT=0
  python3 -c "import fastapi; print('[setup] OK: fastapi')" || VERIFY_RESULT=1
  python3 -c "import uvicorn; print('[setup] OK: uvicorn')" || VERIFY_RESULT=1
  python3 -c "import tos; print('[setup] OK: tos')" || VERIFY_RESULT=1
  
  if [ $VERIFY_RESULT -ne 0 ]; then
    echo "[setup] ERROR: Module verification failed"
    exit 1
  fi
  
  echo "[setup] Deploy installation completed successfully"
else
  echo "[setup] Devbox mode (uv): installing to .venv"
  if [ -f "uv.lock" ]; then
    uv sync --frozen || uv sync
  else
    uv sync
  fi
  touch .venv/.uv_ready
fi
