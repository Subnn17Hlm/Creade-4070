#!/bin/bash
set -eo pipefail

# 时间戳辅助函数
step_start() {
  STEP_START_TIME=$(date +%s)
  echo "[setup] $(date '+%H:%M:%S') $1"
}
step_end() {
  local end_time=$(date +%s)
  local duration=$((end_time - STEP_START_TIME))
  echo "[setup] $(date '+%H:%M:%S') $1 (${duration}s)"
}

# 根据脚本位置计算项目根目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_DIR"
echo "[setup] $(date '+%H:%M:%S') Working directory: $(pwd)"

# 初始化目录
if [ "$COZE_PROJECT_ENV" = "DEV" ]; then
  if [ ! -d "assets" ]; then
    mkdir -p "assets"
  fi
fi

# uv 安装依赖
if [ -n "$PIP_TARGET" ]; then
  echo "[setup] $(date '+%H:%M:%S') Deploy mode: installing to PIP_TARGET"
  
  # 创建 PIP_TARGET 目录
  if ! mkdir -p "$PIP_TARGET"; then
    echo "[setup] ERROR: Failed to create PIP_TARGET directory"
    exit 1
  fi
  
  # 导出依赖并安装
  step_start "Step 1: Exporting dependencies..."
  if ! uv export --frozen --no-hashes --no-dev > /tmp/requirements_export.txt; then
    echo "[setup] ERROR: uv export failed (exit code: $?)"
    exit 1
  fi
  step_end "Step 1 completed: Exported $(wc -l < /tmp/requirements_export.txt) dependencies"
  
  step_start "Step 2: Installing to target directory..."
  if ! uv pip install --no-cache --target "$PIP_TARGET" -r /tmp/requirements_export.txt; then
    echo "[setup] ERROR: uv pip install failed (exit code: $?)"
    exit 1
  fi
  step_end "Step 2 completed"
  
  # 清理临时文件
  rm -f /tmp/requirements_export.txt
  
  # 验证关键模块可导入
  step_start "Step 3: Verifying installation..."
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
  step_end "Step 3 completed"
  
  echo "[setup] $(date '+%H:%M:%S') Deploy installation completed successfully"
else
  step_start "Devbox mode (uv): installing to .venv..."
  if [ -f "uv.lock" ]; then
    uv sync --frozen || uv sync
  else
    uv sync
  fi
  touch .venv/.uv_ready
  step_end "Devbox installation completed"
fi

# 部署前静态校验：确保关键文件存在
step_start "Step 4: Static validation..."
REQUIRED_FILES=(
  "src/main.py"
  "src/graphs/__init__.py"
  "src/graphs/nodes/__init__.py"
  "pyproject.toml"
)
MISSING_FILES=()
for f in "${REQUIRED_FILES[@]}"; do
  if [ ! -f "$f" ]; then
    MISSING_FILES+=("$f")
  fi
done

if [ ${#MISSING_FILES[@]} -gt 0 ]; then
  echo "[setup] ERROR: Missing required files:"
  for f in "${MISSING_FILES[@]}"; do
    echo "  - $f"
  done
  echo "[setup] Deployment package is incomplete"
  exit 1
fi
step_end "Step 4 completed"

echo "[setup] $(date '+%H:%M:%S') All validation passed"
