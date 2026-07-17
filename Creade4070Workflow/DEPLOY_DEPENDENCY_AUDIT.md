# 部署依赖审计报告

## 审计时间
2026-03-26

## 问题背景
部署构建超时（约 94 秒后被终止），原因是安装大量重型依赖：
- torch (~500MB)
- torchvision (~7MB)
- scipy (~34MB)
- scikit-image (~13MB)
- nvidia-cublas/cudnn/cuda-runtime 等 (~1.5GB+)

这些依赖由 `easyocr` 和 `rapidocr-onnxruntime` 引入。

## 依赖来源分析

### 重型依赖链路

| 重型包 | 来源 | 代码中使用 |
|--------|------|------------|
| torch, torchvision | easyocr | 否 |
| scipy, scikit-image | easyocr | 否 |
| nvidia-cublas/cudnn/cuda-* | torch | 否 |
| easyocr | 直接依赖 | 否 |
| rapidocr-onnxruntime | 直接依赖 | 否 |

### 代码扫描结果

```bash
# src 中搜索 OCR 相关 import
grep -r "easyocr|EasyOCR|rapidocr" src/
# 结果：无匹配

# src 中搜索 torch 相关 import
grep -r "import torch|from torch" src/
# 结果：无匹配

# src 中搜索 cv2 使用
grep -r "import cv2|from cv2" src/
# 结果：仅 quality_check_node.py:406 使用（函数内惰性导入）
```

## 精简决策

### 保留的依赖（运行时必需）

| 依赖 | 用途 |
|------|------|
| fastapi, uvicorn | HTTP 服务 |
| langchain, langgraph | 工作流引擎 |
| coze-coding-dev-sdk | 平台 SDK |
| opencv-python | quality_check_node.py 视频处理 |
| Pillow | 图像处理 |
| pandas | 数据处理 |
| boto3, tos | 对象存储 |
| psycopg2-binary, psycopg, sqlalchemy | 数据库 |
| pycairo, dbus-python, PyGObject | 平台依赖（vibe-coding 需要） |

### 移至 dev group 的依赖

| 依赖 | 原因 |
|------|------|
| easyocr>=1.7.2 | 代码中未使用，仅在离线审计脚本中可能使用 |
| rapidocr-onnxruntime>=1.4.4 | 代码中未使用 |

### 保留的重型依赖

| 依赖 | 来源 | 说明 |
|------|------|------|
| opencv-python | 直接依赖 | quality_check_node.py 使用 |
| pycairo | 平台依赖 | vibe-coding 需要 |
| dbus-python | 平台依赖 | vibe-coding 需要 |
| PyGObject | 平台依赖 | vibe-coding 需要 |

## 精简结果

| 指标 | 精简前 | 精简后 | 变化 |
|------|--------|--------|------|
| 导出包数量 | ~584 | 449 | -135 |
| torch | 存在 | 移除 | - |
| torchvision | 存在 | 移除 | - |
| scipy | 存在 | 移除 | - |
| scikit-image | 存在 | 移除 | - |
| nvidia-* | 存在 | 移除 | - |

## 修改文件

| 文件 | 修改内容 |
|------|----------|
| `pyproject.toml` | 将 easyocr、rapidocr-onnxruntime 移至 `[dependency-groups] dev` |
| `uv.lock` | 更新锁文件 |
| `scripts/setup.sh` | 添加时间戳输出 |

## coze SDK 依赖检查

```bash
# 检查 coze-coding-dev-sdk 是否引入 torch
uv export --frozen --no-hashes --no-dev | grep -A20 "^coze-coding-dev-sdk"
# 结果：coze-coding-dev-sdk 不直接依赖 torch
```

torch 是由 easyocr → torchvision 链路引入，与 coze SDK 无关。

## 惰性导入检查

代码中已有的惰性导入：
- `quality_check_node.py:406`: `import cv2` 在函数内

无需额外修改。

## 语法检查

```
42/42 文件通过 Python 语法编译
```

## 部署预期

精简后，`uv export --frozen --no-hashes --no-dev` 将导出 449 个包（原 584 个），减少约 23%。

移除的重型依赖预计可减少约 2GB+ 的下载和安装时间。

## 注意事项

1. 如果后续需要在部署环境使用 OCR 功能，需要：
   - 将 easyocr 移回主依赖
   - 或实现惰性加载（仅在需要时安装）

2. pycairo、dbus-python、PyGObject 是平台依赖，需要部署环境提供相应的系统库：
   - libcairo2-dev
   - libdbus-1-dev
   - libgirepository1.0-dev

3. 如果部署环境缺少这些系统库，可以考虑：
   - 使用预编译的 wheel
   - 或在部署镜像中预装这些库
