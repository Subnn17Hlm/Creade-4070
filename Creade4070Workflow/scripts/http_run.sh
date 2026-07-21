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
if [ -f ".venv/bin/activate" ]; then
  source ".venv/bin/activate"
fi

# 设置 PYTHONPATH 以支持相对导入
export PYTHONPATH="${PROJECT_DIR}/src:${PYTHONPATH:-}"

# 运行数据库迁移（如果数据库 URL 可用）
if [ -n "$PGDATABASE_URL" ] || [ -n "$DATABASE_URL" ]; then
  echo "[http_run] Database URL detected, running migrations..."
  
  # 验证 alembic 模块
  if python -c "import alembic" 2>/dev/null; then
    echo "[http_run] Alembic module available"
    
    # 执行迁移（幂等 - 仅应用待处理的迁移）
    if python -m alembic -c "${PROJECT_DIR}/alembic.ini" upgrade head; then
      echo "[http_run] ✓ Database migrations completed successfully"
    else
      MIGRATION_EXIT_CODE=$?
      echo "[http_run] ERROR: Database migration failed (exit code: $MIGRATION_EXIT_CODE)"
      echo "[http_run] Cannot start server without successful migrations"
      exit $MIGRATION_EXIT_CODE
    fi
  else
    echo "[http_run] WARNING: Alembic module not available, skipping migrations"
  fi
else
  echo "[http_run] WARNING: Database URL not configured, skipping migrations"
fi

# 启动服务
echo "[http_run] Starting server on port $PORT..."
python src/main.py -m http -p $PORT
