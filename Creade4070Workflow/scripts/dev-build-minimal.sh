#!/bin/bash
# 最小化预览构建脚本
# 仅安装 TOS 健康检查服务所需的依赖

set -e

echo "[dev.build] Starting minimal preview build..."

# 定位项目目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "[dev.build] Working directory: $(pwd)"

# 检查 Python
if ! command -v python3 &> /dev/null; then
    echo "[dev.build] ERROR: python3 not found"
    exit 1
fi

echo "[dev.build] Python version: $(python3 --version)"

# 安装最小依赖
echo "[dev.build] Installing minimal dependencies..."

pip install --quiet \
    fastapi \
    uvicorn \
    tos \
    requests

echo "[dev.build] Minimal dependencies installed"

# 验证关键模块
echo "[dev.build] Verifying modules..."

python3 -c "
import fastapi
import uvicorn
import tos
import requests
print('[dev.build] All modules OK')
"

echo "[dev.build] Build completed successfully"
