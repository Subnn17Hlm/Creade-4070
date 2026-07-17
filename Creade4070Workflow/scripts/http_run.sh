#!/bin/bash

set -e

# 根据脚本位置计算项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
echo "[http_run] Working directory: $(pwd)"

PORT="${DEPLOY_RUN_PORT:-80}"

usage() {
  echo "用法: $0 -p <端口>"
}

while getopts "p:h" opt; do
  case "$opt" in
    p)
      PORT="$OPTARG"
      ;;
    h)
      usage
      exit 0
      ;;
    \?)
      echo "无效选项: -$OPTARG"
      usage
      exit 1
      ;;
  esac
done

# 激活 .venv（devbox 环境），deploy 无 .venv 则跳过
if [ -f "${WORK_DIR}/.venv/bin/activate" ]; then
  source "${WORK_DIR}/.venv/bin/activate"
fi

python ${WORK_DIR}/src/main.py -m http -p $PORT
