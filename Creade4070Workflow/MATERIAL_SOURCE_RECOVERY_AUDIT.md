# 素材地址恢复审计报告

| 项目 | 值 |
|------|-----|
| 项目根目录 | `/workspace/projects/Creade4070Workflow/` |
| 审计时间 | 2026-07-17 17:55 CST |
| 审计范围 | 只读扫描，未修改 CSV、未运行工作流、未生成视频、未部署 |

---

## 1. 扫描结果

### 目标文件扫描

| 目标文件/目录 | 状态 |
|--------------|------|
| `runs/**/selected_assets.json` | 不存在（`runs/` 目录不存在） |
| `runs/**/clipped_assets.json` | 不存在 |
| `素材质量优化/safe_assets.json` | 不存在（`素材质量优化/` 目录不存在） |
| `material_audit_detail.json` | 不存在 |

### 实际可用数据源

| 数据源 | 条目数 | 包含 URL | 说明 |
|--------|--------|----------|------|
| `assets/native_text_whitelist.json` | 1 | 是（HTTP URL） | 有完整 TOS 预签名 URL |
| `assets/product_4070_safe_whitelist.json` | 21 | 否（`url` 字段为数字字符串） | 仅含 asset_id 和 primary_scene_tag |

---

## 2. 匹配统计

### 按 asset_manifest_v2_clean.csv 的 126 条记录为基准

| 类别 | 数量 | 说明 |
|------|------|------|
| **CSV 总记录** | 126 | 基准 |
| **可匹配的唯一素材** | 21 | 在至少一个数据源中找到匹配的 asset_id |
| **有完整 HTTP URL** | 1 | 仅 `屏显调温_003`（来自 native_text_whitelist.json） |
| **有 bucket+object_key** | 0 | 所有数据源均无此信息 |
| **无有效 URL** | 20 | 来自 product_4070_safe_whitelist.json，`url` 字段为数字（如 "3"、"5"），非真实 URL |
| **完全无法恢复** | 105 | 不在任何数据源中，无任何元数据可供恢复 |

### 比例

| 指标 | 比例 |
|------|------|
| 可匹配率 | 21/126 = 16.7% |
| 有 URL 可恢复率 | 1/126 = 0.8% |
| 完全无法恢复率 | 105/126 = 83.3% |

---

## 3. 有完整 HTTP URL 的素材（1 条）

| asset_id | file_name | primary_scene_tag | URL 来源 | URL 状态 |
|----------|-----------|-------------------|----------|----------|
| 屏显调温_003 | 屏显调温_003_温度模式_3s.mp4 | 屏显调温 | native_text_whitelist.json | TOS 预签名 URL（sign=1784224524，待确认是否过期） |

> URL 格式: `https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/...?sign=1784224524-...`
> sign 中的时间戳 `1784224524` 对应约 2026-07-12，**可能已过期或即将过期**，待确认。

---

## 4. 无有效 URL 的已匹配素材（20 条）

以下素材在 `product_4070_safe_whitelist.json` 中有 asset_id 和 primary_scene_tag，但无可用 URL：

| # | asset_id | primary_scene_tag |
|---|----------|-------------------|
| 1 | 产品展示_001 | 产品展示 |
| 2 | 产品展示_002 | 产品展示 |
| 3 | 吹发动作_001 | 吹发动作 |
| 4 | 吹发动作_008 | 吹发动作 |
| 5 | 屏显调温_001 | 屏显调温 |
| 6 | 屏显调温_007 | 屏显调温 |
| 7 | 屏显调温_008 | 屏显调温 |
| 8 | 手持大小对比_001 | 手持大小对比 |
| 9 | 手持大小对比_002 | 手持大小对比 |
| 10 | 手持大小对比_003 | 手持大小对比 |
| 11 | 手持大小对比_006 | 手持大小对比 |
| 12 | 折叠动作_001 | 折叠动作 |
| 13 | 折叠动作_002 | 折叠动作 |
| 14 | 放进包包_005 | 放进包包 |
| 15 | 放进行李箱_005 | 放进行李箱 |
| 16 | 风力展示_001 | 风力展示 |
| 17 | 风力展示_002 | 风力展示 |
| 18 | 风力展示_004 | 风力展示 |
| 19 | 风力展示_005 | 风力展示 |
| 20 | 风嘴配件_007 | 风嘴配件 |

---

## 5. 完全无法恢复的素材（105 条）

以下 105 个 asset_id 不在任何数据源中，无法从当前项目文件中恢复：

> 待确认：这些素材的原始 URL 信息是否存在于项目外部（如对象存储控制台、本地素材库、其他文档）。

---

## 6. 约束确认

| 约束 | 状态 |
|------|------|
| 未把截取后的临时 clip 路径冒充原始素材地址 | 已遵守（无 clip 文件存在） |
| 未直接写回 asset_manifest_v2_clean.csv | 已遵守 |
| 未运行工作流 | 已遵守 |
| 未生成视频 | 已遵守 |

---

## 7. 输出文件

| 文件 | 路径 | 说明 |
|------|------|------|
| 审计报告 | `MATERIAL_SOURCE_RECOVERY_AUDIT.md` | 本文件 |
| 候选清单 | `recovery_candidates.csv` | 21 条可匹配素材，含字段：asset_id, file_name, primary_scene_tag, source_url, s3_url, bucket, object_key, local_path, url_source, url_status, recovery_status |

### recovery_candidates.csv 字段说明

| 字段 | 说明 |
|------|------|
| `asset_id` | 素材 ID |
| `file_name` | 文件名 |
| `primary_scene_tag` | 场景标签 |
| `source_url` | 恢复的 HTTP URL（仅 1 条有值） |
| `s3_url` | S3 URL（全部为空） |
| `bucket` | S3 bucket（全部为空） |
| `object_key` | S3 object key（全部为空） |
| `local_path` | 本地路径（全部为空） |
| `url_source` | URL 来源文件 |
| `url_status` | URL 状态：`http_ok` / `missing` / `invalid` |
| `recovery_status` | 恢复状态：`recoverable`（有 URL）/ `no_url`（无 URL） |

---

## 8. 结论

当前项目中可恢复的素材地址信息极度匮乏：

- **仅 1 条素材**（屏显调温_003）有完整 HTTP URL，但该 URL 为 TOS 预签名链接，可能已过期
- **20 条素材**有 asset_id 和标签信息，但无任何可用的视频地址
- **105 条素材**（83.3%）完全无法从当前项目文件中恢复

**建议**: 需要从项目外部获取完整的素材 URL 清单，或重新上传素材到对象存储并生成新的 URL。
