#!/bin/bash
# 最小化预览运行脚本
# 启动独立的 TOS 健康检查服务

set -e

echo "[dev.run] Starting minimal preview server..."

# 定位项目目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"

echo "[dev.run] Working directory: $(pwd)"

# 设置 PYTHONPATH
export PYTHONPATH="$PROJECT_DIR/src:$PYTHONPATH"

# 设置端口
export PORT="${PORT:-5000}"

echo "[dev.run] PORT: $PORT"

# 清理可能存在的旧进程
pkill -f "preview_server.py" 2>/dev/null || true
sleep 1

# 启动服务
echo "[dev.run] Starting preview server..."
exec python3 src/preview_server.py
