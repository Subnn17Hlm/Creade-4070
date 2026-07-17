# 素材绑定修复报告

| 项目 | 值 |
|------|-----|
| 项目根目录 | `/workspace/projects/Creade4070Workflow/` |
| 执行时间 | 2026-07-17 18:10 CST |
| 目标存储桶 | `coze-video-assets-hlm` |
| 对象前缀 | `materials_v2/` |
| 限制 | 不运行工作流、不生成视频、不部署 |

---

## 1. 生成文件

### assets/asset_manifest_v2_bound.csv

| 属性 | 值 |
|------|-----|
| 记录数 | 126 |
| 字段数 | 11 |
| 新增字段 | `bucket`, `object_key` |
| 原始文件 | `assets/asset_manifest_v2_clean.csv`（未修改） |

### 字段列表

```
asset_id, file_name, primary_scene_tag, duration_sec, description,
source_url, local_path, enabled, notes, bucket, object_key
```

### 绑定规则

- `bucket` = `coze-video-assets-hlm`（全部 126 条）
- `object_key` = `materials_v2/<file_name>`（全部 126 条）
- 示例：`materials_v2/折叠动作_001_折叠收起_3s.mp4`

---

## 2. 代码修改

### 2.1 默认清单路径修改（4 处）

| 文件 | 行号 | 修改内容 |
|------|------|----------|
| `src/graphs/state.py` | 117 | `Field(default="assets/asset_manifest_v2_bound.csv")` |
| `src/graphs/nodes/material_matching_node.py` | 590 | 回退默认路径改为 `asset_manifest_v2_bound.csv` |
| `src/pipeline/single_run.py` | 8 | 示例代码路径改为 `asset_manifest_v2_bound.csv` |
| `scripts/material_quality_audit.py` | 81~82 | 审计脚本路径改为 `asset_manifest_v2_bound.csv` |

### 2.2 素材读取逻辑修改

#### material_matching_node.py

新增函数：

| 函数 | 用途 |
|------|------|
| `_resolve_material_url(row)` | 按优先级解析 URL：source_url > s3_url > local_path |
| `_get_presigned_url(bucket, object_key, expire_time=1800)` | 运行时通过 S3SyncStorage 生成预签名 URL |

修改 `_load_material_manifest()`：
- 过滤条件从 `if mat["s3_url"]` 改为 `if url or (bucket and object_key)`
- 有 bucket+object_key 但无 URL 的记录也会被保留

修改匹配结果构建（第 1048 行）：
- `selected_url` 解析逻辑：优先使用 `s3_url`，为空时通过 `_get_presigned_url()` 运行时生成

#### material_source_audit_node.py

修改 CSV 读取逻辑（第 89~103 行）：
- URL 解析优先级：source_url > s3_url > presigned_url > bucket+object_key(运行时预签名) > local_path
- 当 URL 为空但有 bucket+object_key 时，调用 `_get_presigned_url()` 生成临时 URL

### 2.3 安全约束

| 约束 | 状态 |
|------|------|
| AccessKey/SecretKey 未写入代码 | 已遵守 |
| 固定预签名 URL 未写入 CSV | 已遵守 |
| 预签名 URL 仅在运行时生成 | 已遵守 |
| 有效期 | 1800 秒（30 分钟） |
| 复用现有 S3SyncStorage.generate_presigned_url() | 已遵守 |

---

## 3. 对象存在性检查（HEAD）

### 检查环境

| 参数 | 值 |
|------|-----|
| 目标 bucket | `coze-video-assets-hlm` |
| 环境配置 bucket | `bucket_1784279228652` |
| endpoint | `https://integration.coze.cn/coze-coding-s3proxy/v1` |

### 检查结果

| 类别 | 数量 | 说明 |
|------|------|------|
| 总记录 | 126 | |
| 成功匹配（对象存在） | 0 | |
| 文件不存在（404） | 0 | bucket 级别返回 NoSuchBucket |
| 权限失败（403） | 0 | |
| bucket 不存在 | **126** | `coze-video-assets-hlm` 在当前环境中不存在 |

### 分析

HEAD 请求对 `coze-video-assets-hlm` 返回 404，但通过 `list_objects_v2` 检查发现该 bucket 返回 `NoSuchBucket` 错误。当前环境配置的 bucket 是 `bucket_1784279228652`，与目标 bucket 不同。

**结论**: 存储桶 `coze-video-assets-hlm` 在当前部署环境中尚未创建或不可访问。素材文件需要先上传到该 bucket 的 `materials_v2/` 前缀下。

> 注：这不是权限错误（403），而是 bucket 不存在。无法判定单个文件是否存在。

---

## 4. 语法编译检查

| 检查范围 | 结果 |
|----------|------|
| `src/**/*.py` + `scripts/*.py` | **40/40 通过** |
| 失败文件 | 无 |

---

## 5. 修改文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `assets/asset_manifest_v2_bound.csv` | 新增 | 126 条记录，含 bucket/object_key 绑定 |
| `src/graphs/state.py` | 修改 | 默认路径改为 bound CSV |
| `src/graphs/nodes/material_matching_node.py` | 修改 | 新增 URL 解析函数，修改过滤和匹配逻辑 |
| `src/graphs/nodes/material_source_audit_node.py` | 修改 | 修改 URL 解析优先级 |
| `src/pipeline/single_run.py` | 修改 | 示例路径改为 bound CSV |
| `scripts/material_quality_audit.py` | 修改 | 审计路径改为 bound CSV |

---

## 6. 运行时 URL 解析流程

```
加载 CSV 记录
    │
    ├── source_url 非空？ → 直接使用
    │
    ├── s3_url 非空？ → 直接使用
    │
    ├── bucket + object_key 都有？ → 调用 _get_presigned_url()
    │       │                          生成临时签名 URL（1800s）
    │       └── 失败？ → 记录警告，该素材跳过
    │
    ├── local_path 非空？ → 使用本地路径
    │
    └── 全部为空 → 素材不可用，跳过
```

---

## 7. 待完成事项

| # | 事项 | 优先级 |
|---|------|--------|
| 1 | 创建存储桶 `coze-video-assets-hlm` 或确认正确的 bucket 名称 | **P0** |
| 2 | 将 126 个素材视频上传到 `materials_v2/<file_name>` | **P0** |
| 3 | 上传完成后重新执行 HEAD 检查确认对象存在 | P1 |
| 4 | 确认 `COZE_BUCKET_NAME` 环境变量指向正确的 bucket | P1 |
| 5 | 运行完整工作流验证端到端流程 | P2 |

---

## 8. 统计汇总

| 指标 | 数量 |
|------|------|
| CSV 总记录 | 126 |
| 成功绑定 bucket+object_key | 126 |
| 对象存在（HEAD 成功） | 0 |
| bucket 不存在 | 126 |
| 权限失败 | 0 |
| 文件名不一致 | 0（待 bucket 创建后重新检查） |
| 语法编译通过 | 40/40 |
