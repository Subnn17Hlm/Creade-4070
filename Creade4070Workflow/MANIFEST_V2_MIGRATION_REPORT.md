# Manifest V2 迁移报告

| 项目 | 值 |
|------|-----|
| 项目根目录 | `/workspace/projects/Creade4070Workflow/` |
| 执行时间 | 2026-07-17 17:50 CST |
| 执行范围 | 最小修改，仅更新默认文件引用，未运行工作流 |

---

## 1. 修改文件清单

### 源代码修改

| 文件 | 行号 | 修改内容 |
|------|------|----------|
| `src/graphs/state.py` | 117 | `GraphInput.material_csv` 默认值从 `asset_manifest_new_no_chuifa.csv` 改为 `asset_manifest_v2_clean.csv` |
| `src/graphs/nodes/material_matching_node.py` | 590 | 回退默认路径从 `asset_manifest_new_no_chuifa.csv` 改为 `asset_manifest_v2_clean.csv` |
| `src/pipeline/single_run.py` | 8 | 示例代码中的默认路径改为 `asset_manifest_v2_clean.csv` |
| `scripts/material_quality_audit.py` | 81 | 审计脚本中的默认 CSV 路径改为 `asset_manifest_v2_clean.csv` |

### 文档更新

| 文件 | 修改内容 |
|------|----------|
| `AGENTS.md` | 资源文件描述从 73 个素材更新为 126 个素材，文件名更新 |

### 未修改

- 未删除 `asset_manifest_new_no_chuifa.csv` 的引用历史（文档文件 `STATIC_CONFIGURATION_AUDIT.md`、`FIRST_STAGE_REPAIR_REPORT.md` 保留原样，作为历史记录）
- 未修改 CSV 内容
- 未生成兼容旧表

---

## 2. 新 CSV 字段兼容性分析

### 新 CSV 字段

```
asset_id, file_name, primary_scene_tag, duration_sec, description, source_url, local_path, enabled, notes
```

### 读取逻辑兼容性

| 代码位置 | 读取的字段 | 兼容性 | 说明 |
|----------|-----------|--------|------|
| `material_matching_node.py:322~352` `_load_material_manifest()` | `deprecated`, `enabled`, `source_url`, `s3_url`, `duration_sec`, `asset_id`, `file_name`, `primary_scene_tag`, `bucket`, `object_key`, `description`, `needs_clip`, `notes`, `batch` | **部分兼容** | 新 CSV 缺少 `deprecated`, `bucket`, `object_key`, `s3_url`, `needs_clip`, `batch`, `tags`；代码使用 `row.get()` 安全读取，缺失字段返回空字符串，不会崩溃 |
| `material_source_audit_node.py:92~97` | `asset_id`, `s3_url`, `presigned_url`, `file_name`, `tags` | **不兼容** | 新 CSV 无 `s3_url`、`presigned_url`、`tags` 字段，URL 将为空 |
| `state.py:117` `GraphInput` | `material_csv`（路径） | 兼容 | 仅引用文件路径 |

### 关键兼容问题

**`_load_material_manifest()` 第 351 行过滤逻辑**:

```python
if mat["s3_url"]:
    materials.append(mat)
```

新 CSV 中 `source_url` 全部为空（0/126），且无 `s3_url` 字段，导致 `url` 变量为空，**所有 126 条记录都会被过滤掉**，`_load_material_manifest()` 返回空列表。

**后果**: `material_matching_node` 会记录 "0 个可用素材"，所有句子的素材匹配都会失败。

---

## 3. source_url 和 local_path 为空时的工作流停止点

### 数据流追踪

```
CSV (source_url=空, local_path=空)
  ↓
_load_material_manifest() → url = "" → 被 if mat["s3_url"] 过滤
  ↓
all_materials = [] (空列表)
  ↓
material_matching_node → 所有句子匹配失败 → selected_url = ""
  ↓
clip_extraction_node → material_url = "" → 跳过该片段 (status="skipped_no_url")
  ↓
timeline_assembly → 无有效 clip
  ↓
final_composition → 无素材可拼接 → 输出空视频或失败
  ↓
quality_check → 验收失败
```

### 具体停止点

| 节点 | 行为 | 严重程度 |
|------|------|----------|
| **material_matching_node** (Node4) | `all_materials = 0`，所有句子走 fallback 仍无素材可匹配，`selected_url` 全为空 | **致命** — 工作流实质停止 |
| **clip_extraction_node** (Node5) | `material_url` 为空，每个片段记录 `status="skipped_no_url"`，不生成 clip | 全部跳过 |
| **final_composition_node** (Node7) | 无 clip 可拼接，ffmpeg concat 失败或输出空文件 | 失败 |
| **quality_check_node** (Node8) | `final.mp4` 不存在或无效，`status=failed` | 失败 |

**结论**: 工作流在 **material_matching_node (Node4)** 实质停止。虽然不会抛出异常（代码有容错），但后续所有节点都无有效数据可处理，最终输出 `status=failed`。

---

## 4. file_name 自动绑定机制检查

### 检查结果

**不存在通过 `file_name` 自动绑定已上传素材或对象存储文件的机制。**

具体分析：

| 检查项 | 结果 |
|--------|------|
| 代码中是否有 `file_name → URL` 的解析逻辑 | 否 |
| 是否有通过 `file_name` 查询 S3/对象存储的代码 | 否 |
| `local_path` 字段是否被任何节点读取 | 否（`_load_material_manifest` 未读取 `local_path`） |
| 是否有基于 `bucket` + `object_key` 构建 URL 的逻辑 | 代码中有读取 `bucket`/`object_key` 字段，但新 CSV 不包含这些字段 |
| `media_uploader.py` 是否支持反向查询 | 否，仅支持上传（`upload_local_file`） |

**结论**: 当前代码完全依赖 CSV 中的 `source_url`（或旧格式的 `s3_url`）来获取素材视频。`file_name` 和 `local_path` 仅作为元数据记录，不参与素材定位。

---

## 5. 解决方案建议

为使 `asset_manifest_v2_clean.csv` 正常工作，需要以下之一：

### 方案 A：填充 source_url（推荐）

在 CSV 中为每条记录填充 `source_url`，指向可访问的视频 URL（S3 预签名 URL 或 HTTP URL）。

### 方案 B：修改代码支持 local_path

在 `_load_material_manifest()` 中增加 `local_path` 回退逻辑：

```python
url = row.get("source_url", "").strip() or row.get("s3_url", "").strip() or row.get("local_path", "").strip()
```

并在 `clip_extraction_node` 中支持本地文件路径。

### 方案 C：修改代码支持 file_name 自动绑定

在 `_load_material_manifest()` 中增加基于 `file_name` 的对象存储查询逻辑，自动构建 URL。需要知道素材在对象存储中的 bucket 和路径规则。

### 方案 D：修改过滤逻辑

将 `_load_material_manifest()` 第 351 行的过滤条件改为允许无 URL 的记录通过（仅用于标签匹配），在 `clip_extraction_node` 中再处理 URL 缺失的情况。

---

## 6. Python 语法编译结果

修改后重新编译全部 40 个 `.py` 文件：

**结果**: 40/40 通过，0 失败。

---

## 7. 新 CSV 数据统计

| 指标 | 值 |
|------|-----|
| 总行数 | 126 |
| `enabled=true` | 126 |
| `enabled=false` | 0 |
| `source_url` 非空 | 0 |
| `local_path` 非空 | 0 |
| 唯一 `primary_scene_tag` 数 | 待确认 |

### 缺失的旧字段

| 字段 | 旧 CSV 有 | 新 CSV 有 | 代码是否使用 |
|------|-----------|-----------|-------------|
| `deprecated` | 是 | 否 | 是（过滤用，缺失时默认不过滤） |
| `bucket` | 是 | 否 | 是（读取但非必需） |
| `object_key` | 是 | 否 | 是（读取但非必需） |
| `s3_url` | 是 | 否 | 是（作为 source_url 的回退） |
| `needs_clip` | 是 | 否 | 是（缺失时默认 false） |
| `batch` | 是 | 否 | 是（仅记录） |
| `tags` | 是 | 否 | 是（素材源预检使用） |

### 新增字段

| 字段 | 说明 |
|------|------|
| `local_path` | 本地文件路径（当前全部为空，代码未读取） |

---

## 8. 下一步操作

1. **确定素材 URL 填充方案** — 选择上述方案 A/B/C/D 之一
2. **填充 source_url 或修改代码** — 使 126 条素材可被工作流访问
3. **验证素材匹配** — 确认标签匹配逻辑与新 CSV 的 `primary_scene_tag` 兼容
4. **运行完整工作流测试** — 确认端到端流程通过
