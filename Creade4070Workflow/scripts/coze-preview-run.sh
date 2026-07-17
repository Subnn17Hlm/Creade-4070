#!/usr/bin/env bash
set -euo pipefail

# 基于脚本位置定位项目根目录
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

# 显式声明关键环境变量
export PORT=5000
export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH:-}"

# 创建 langchain.callbacks 兼容性 shim
# langchain 1.0+ 移除了 langchain.callbacks，但平台包依赖它
# 使用 sitecustomize.py 在 Python 启动时自动加载
SITE_DIR="${PROJECT_DIR}/src/_preview_site"
mkdir -p "$SITE_DIR"
cat > "$SITE_DIR/sitecustomize.py" << 'SHIM_EOF'
"""Compatibility shim for langchain.callbacks"""
import sys
import langchain_core.callbacks

# Create langchain.callbacks module alias
sys.modules['langchain.callbacks'] = langchain_core.callbacks
sys.modules['langchain.callbacks.base'] = langchain_core.callbacks.base
sys.modules['langchain.callbacks.manager'] = langchain_core.callbacks.manager

# Also patch langchain module
import langchain
langchain.callbacks = langchain_core.callbacks
SHIM_EOF

# 设置 PYTHONPATH 包含 shim 目录
export PYTHONPATH="${SITE_DIR}:${PYTHONPATH}"

echo "[coze-preview-run] Working directory: $(pwd)"
echo "[coze-preview-run] PYTHONPATH: $PYTHONPATH"
echo "[coze-preview-run] Starting preview server on 0.0.0.0:5000"

# 清理 5000 端口残留进程（绝不碰 9000）
fuser -k 5000/tcp 2>/dev/null || true
sleep 1

# 启动预览服务
exec python src/main.py -m http -p 5000
