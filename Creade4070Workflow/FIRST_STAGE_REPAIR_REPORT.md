# 第一阶段最小修复报告

| 项目 | 值 |
|------|-----|
| 项目根目录 | `/workspace/projects/Creade4070Workflow/` |
| 执行时间 | 2026-07-17 17:35 CST |
| 执行范围 | 最小修复，未运行工作流、未生成视频、未部署 |

---

## 1. 修改文件清单

### 已修改

| 文件 | 修改内容 |
|------|----------|
| `src/graphs/nodes/timeline_assembly_node.py` | 第 224 行：将 `"素材质量优化"` 改为 `"assets"`，使白名单路径指向实际存在的 `assets/native_text_whitelist.json` |

### 修改前

```python
whitelist_path = os.path.join(workspace_path, "素材质量优化", "native_text_whitelist.json")
```

### 修改后

```python
whitelist_path = os.path.join(workspace_path, "assets", "native_text_whitelist.json")
```

### 未修改（按要求保留）

| 文件 | 说明 |
|------|------|
| `scripts/material_quality_audit.py` | 审计输出目录路径保留原样 |
| `scripts/validate_delivery_artifacts.py` | 审计输出目录路径保留原样 |
| `assets/product_4070_safe_whitelist.json` | 内嵌证据路径保留原样 |

---

## 2. native_text_whitelist.json 验证结果

| 检查项 | 结果 |
|--------|------|
| 文件路径 | `assets/native_text_whitelist.json` |
| 文件大小 | 2204 bytes |
| JSON 解析 | 通过 |
| 数据类型 | `list` |
| 条目数 | 1 |
| 条目详情 | `asset_id=屏显调温_003`, `native_text_allowed=True`, `suppress_generated_subtitle=True` |

**结论**: 文件可正常解析，修复后 `timeline_assembly_node.py` 可正确读取该白名单。

---

## 3. 审计目录建议

当前 `scripts/material_quality_audit.py` 和 `scripts/validate_delivery_artifacts.py` 引用 `素材质量优化/` 目录作为审计输出位置。该目录不存在，但不影响工作流运行。

### 建议方案

| 方案 | 说明 | 推荐 |
|------|------|------|
| **A. 保留独立审计目录** | 创建 `素材质量优化/` 目录，专门存放审计产物（证据帧、审计报告）。与工作流运行目录 `runs/` 分离，职责清晰 | 推荐 |
| B. 迁移到 `assets/` | 将审计产物放入 `assets/` 目录。缺点：`assets/` 是静态资源目录，审计产物是动态生成的，混在一起不清晰 | 不推荐 |
| C. 改为 `reports/` 目录 | 创建独立的 `reports/` 目录存放所有审计报告。语义更明确，但需要修改脚本路径 | 可选 |

**当前建议**: 采用方案 A，保留 `素材质量优化/` 作为审计专用目录。该目录仅被审计脚本使用，不影响工作流核心链路。在需要执行审计时手动创建即可。

---

## 4. FFmpeg 安装方式评估

### 环境检测结果

| 检查项 | 结果 |
|--------|------|
| `apt-get` | 可用（`/usr/bin/apt-get`） |
| Dockerfile | 不存在 |
| `.coze` 中 `deploy.deps` | 未配置 |
| 平台系统依赖配置 | 未配置 |

### 推荐安装方式

| 优先级 | 方式 | 说明 |
|--------|------|------|
| **1（推荐）** | 在 `scripts/setup.sh` 中增加 `apt-get install -y ffmpeg` | 当前环境 apt-get 可用，且 setup.sh 是部署构建的标准入口。在 Python 依赖安装前执行系统包安装 |
| 2 | 在 `.coze` 中添加 `deploy.deps = ["ffmpeg"]` | 待确认平台是否支持 `deploy.deps` 自动安装系统包 |
| 3 | 创建 Dockerfile | 当前无 Dockerfile，且项目通过平台部署，创建 Dockerfile 可能不被平台识别 |

### 建议的 setup.sh 修改

在 `uv sync` 之前增加：

```bash
# 安装系统依赖
if command -v apt-get >/dev/null 2>&1; then
  if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "[setup] Installing ffmpeg..."
    apt-get update -qq && apt-get install -y -qq ffmpeg
  fi
fi
```

**注意**: 本次未实际修改 setup.sh，仅给出建议。

---

## 5. CSV 可恢复情况

### 数据源扫描

| 数据源 | 文件 | 条目数 | 说明 |
|--------|------|--------|------|
| runs/ 目录 | - | 0 | 目录不存在 |
| selected_assets.json | - | 0 | 文件不存在 |
| clipped_assets.json | - | 0 | 文件不存在 |
| safe_assets.json | - | 0 | 文件不存在 |
| asset_manifest*.csv | - | 0 | 文件不存在 |
| `assets/native_text_whitelist.json` | 存在 | 1 | 有完整 URL |
| `assets/product_4070_safe_whitelist.json` | 存在 | 21 | 缺少视频 URL |

### 可恢复记录统计

| 来源 | 可恢复数 | 字段完整度 | 缺少的关键字段 |
|------|----------|------------|----------------|
| `native_text_whitelist.json` | 1 条 | 高 | 无（有 asset_id, file_name, url, duration_sec, primary_scene_tag） |
| `product_4070_safe_whitelist.json` | 21 条 | 低 | `file_name`（0/21）、`source_url`（0/21，`url` 字段值为数字非 URL）、`duration_sec`（0/21） |

### 详细分析

**native_text_whitelist.json（1 条完整记录）**:

| 字段 | 值 | CSV 对应字段 |
|------|-----|-------------|
| `asset_id` | 屏显调温_003 | `asset_id` |
| `file_name` | 屏显调温_003_温度模式_3s.mp4 | `file_name` |
| `url` | https://coze-coding-project.tos.coze.site/... | `source_url` |
| `duration_sec` | 3.0 | `duration_sec` |
| `primary_scene_tag` | 屏显调温 | `primary_scene_tag` |

**product_4070_safe_whitelist.json（21 条部分记录）**:

- 有 `asset_id`（21/21）和 `primary_scene_tag`（21/21）
- `url` 字段值为数字字符串（如 "3"、"5"），非视频 URL，**不可用于素材访问**
- 缺少 `file_name`、`source_url`、`duration_sec`
- `evidence_frame_paths` 指向不存在的 `素材质量优化/material_audit_evidence/` 目录

### 结论

| 指标 | 值 |
|------|-----|
| 可完全恢复的素材记录 | **1 条**（屏显调温_003） |
| 可部分恢复的素材记录 | **21 条**（有 asset_id 和 tag，缺视频 URL） |
| 目标 CSV 总条目数 | 73 条（据 AGENTS.md） |
| 缺口 | 至少 52 条素材的完整信息需要从原始素材清单或其他数据源获取 |

**不可将这 22 条记录冒充完整的 73 条 CSV**。需要用户提供原始的 `asset_manifest_new_no_chuifa.csv` 或等效数据源。

---

## 6. Python 语法编译结果

修复后重新编译全部 40 个 `.py` 文件：

```
[OK] scripts/load_env.py
[OK] scripts/material_quality_audit.py
[OK] scripts/validate_delivery_artifacts.py
[OK] src/__init__.py
[OK] src/agents/__init__.py
[OK] src/graphs/__init__.py
[OK] src/graphs/graph.py
[OK] src/graphs/nodes/__init__.py
[OK] src/graphs/nodes/clip_extraction_node.py
[OK] src/graphs/nodes/final_composition_node.py
[OK] src/graphs/nodes/generate_script_node.py
[OK] src/graphs/nodes/input_normalization_node.py
[OK] src/graphs/nodes/manual_script_node.py
[OK] src/graphs/nodes/material_matching_node.py
[OK] src/graphs/nodes/material_source_audit_node.py
[OK] src/graphs/nodes/quality_check_node.py
[OK] src/graphs/nodes/script_source_router_node.py
[OK] src/graphs/nodes/subtitle_timing_node.py
[OK] src/graphs/nodes/timeline_assembly_node.py  ← 已修改
[OK] src/graphs/nodes/tts_generation_node.py
[OK] src/graphs/shared_utils.py
[OK] src/graphs/state.py
[OK] src/main.py
[OK] src/pipeline/__init__.py
[OK] src/pipeline/single_run.py
[OK] src/storage/__init__.py
[OK] src/storage/database/__init__.py
[OK] src/storage/database/db.py
[OK] src/storage/database/shared/__init__.py
[OK] src/storage/database/shared/model.py
[OK] src/storage/memory/__init__.py
[OK] src/storage/memory/memory_saver.py
[OK] src/storage/s3/__init__.py
[OK] src/storage/s3/s3_storage.py
[OK] src/tools/__init__.py
[OK] src/utils/__init__.py
[OK] src/utils/file/__init__.py
[OK] src/utils/file/file.py
[OK] src/utils/media_uploader.py
[OK] src/utils/storage_helper.py
```

**结果**: 40/40 通过，0 失败。

---

## 7. 待确认事项

| # | 事项 | 状态 |
|---|------|------|
| 1 | `asset_manifest_new_no_chuifa.csv` 原始文件 | 待用户提供 |
| 2 | 21 条白名单素材的视频 URL | 待确认数据来源 |
| 3 | 部署环境是否预装 ffmpeg | 待确认目标部署镜像 |
| 4 | 平台是否支持 `deploy.deps` 安装系统包 | 待确认平台能力 |
| 5 | `素材质量优化/` 目录是否需要保留 | 建议保留为审计专用目录 |

---

## 8. 下一步操作

1. 用户提供 `asset_manifest_new_no_chuifa.csv` 原始文件
2. 在 `scripts/setup.sh` 中增加 ffmpeg 安装逻辑
3. 修复剩余 3 处 `素材质量优化` 路径引用（如需要）
4. 安装依赖并验证工作流
