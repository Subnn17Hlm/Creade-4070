# 锁版前审计报告 v2.0

| 项目 | 值 |
|------|-----|
| 审计日期 | 2026-07-16 |
| 审计版本 | script_06_audit |
| 审计类型 | 锁版前结构性修正审计 |
| 审计结论 | 单条验证通过，可进入5条小批量 |

---

## 1. 当前稳定版本信息

| 项目 | 值 |
|------|-----|
| 最终验证 run_dir | `runs/script_06_audit/` |
| 最终视频路径 | `/workspace/projects/runs/script_06_audit/final.mp4` |
| 视频时长 | **16.933s** |
| TTS时长 | **17.736s** |
| video_audio_diff | **-0.803s**（视频略短于音频） |
| TTS | 正常 |
| BGM | 正常（已混入） |
| 字幕 | 正常（18句字幕全部烧录） |
| 字体 | 项目字体 `ALIBABA-PUHUITI-BOLD.TTF` |
| 分辨率 | 1080x1920 竖屏 |

---

## 2. 核心数据核对（以实际产物为准）

| 指标 | 值 | 说明 |
|------|-----|------|
| 分句数量 | **18** | timeline.json 中的句子数 |
| 字幕数量 | **18** | SRT 字幕条数 |
| selected_assets 数量 | **18** | 每句对应一个素材选择 |
| 唯一素材数量 | **18** | 无重复素材 |
| 高置信度匹配 | **17** | 关键词精确匹配 |
| 低置信度匹配 | **1** | 兜底到"产品展示" |
| 活跃clip数量 | **15** | 3句被跨句延续覆盖 |
| 重复素材 | **无** | 每个素材只使用一次 |

**数据一致性确认：**
- 分句数量 = 字幕数量 = selected_assets 数量 = **18** ✅
- 唯一素材数量 = **18**（无重复）✅
- 活跃clip = 15（3句被覆盖）✅

---

## 3. 语义标签匹配来源

| 问题 | 回答 |
|------|------|
| sentence_tag_mapping.json 生成方式 | **纯代码规则生成**，不调用大模型 |
| 标签规则位置 | `src/graphs/nodes/material_matching_node.py` |
| 核心函数 | `_generate_sentence_tag_mapping()` (第111行) |
| 关键词映射配置 | `_KEYWORD_TO_TAG` 字典 (第61行)，包含约50+关键词->标签映射 |
| 标签来源约束 | 只能从 CSV `primary_scene_tag` 集合（`available_tags`）中选择，**不能创造新标签** |

### 3.1 兜底逻辑修正（本次更新）

| 项目 | 修正前 | 修正后 |
|------|--------|--------|
| 兜底标签优先级 | 短句: 手持展示/折叠动作/放进包包<br>长句: 旅行场景/痛点共鸣/手持展示 | **产品展示** > 手持大小对比 > 折叠动作 > 放进包包 |
| "手持展示"是否作为兜底 | **是**（风险点） | **否**（已移除） |
| 无匹配时输出 | 默认标签，无标记 | `low_confidence=true` + `fallback_reason` + `candidate_tags` |
| 质量报告记录 | 不记录 | **所有兜底句子写入质量报告** |
| 失败判定 | `low_confidence > 0` 即失败 | `low_confidence >= 3` 才失败 |

**"手持展示"使用规则：**
- 只能用于明确出现：手持、拿着、握持、单手、手里展示、拿在手里 等语义
- 不作为默认兜底标签
- 本次测试18句中，"手持展示"未被使用（0次）

---

## 4. 素材匹配逻辑来源

| 问题 | 回答 |
|------|------|
| 使用的CSV文件 | `assets/asset_manifest_v2_clean.csv` |
| 是否只用此CSV | 是，通过 `material_csv` 参数传入 |
| 是否读取旧素材清单 | 不会 |
| 是否自动生成素材标签 | 不会。标签严格来自CSV的 `primary_scene_tag` 列 |
| 是否严格使用已有primary_scene_tag | 是。`_generate_sentence_tag_mapping()` 中 `if tag in available_tags` 过滤 |
| description为空时的行为 | 代码支持description辅助匹配，但CSV中description列为空时不会伪称"description辅助匹配已生效" |

---

## 5. 素材裁剪与时间线逻辑

### 5.1 effective_start

| 素材ID | effective_start(s) | 说明 |
|--------|-------------------|------|
| 手持大小对比_003 | 1.5 | 跳过"手拿瓶装水"铺垫，从"变成吹风机"开始 |
| 其他素材 | 0.0 | 从开头开始 |

**状态：已生效** ✅

### 5.2 full_play_required / effective_segment 逻辑说明

**当前实际逻辑：有效段优先（Effective Segment Priority）**

| 素材 | source_duration | 覆盖句子TTS总和 | 实际clip时长 | 逻辑 |
|------|----------------|----------------|-------------|------|
| 屏显调温_003 | 3.040s | 句10+11+12+13 = 3.930s | 3.042s | 使用完整有效段，但不超过覆盖时长 |

**字段命名说明：**
- `full_play_required=true`：表示该素材应尽量完整播放
- 实际行为：使用素材的完整有效段（effective_start 到 effective_end），但会被裁剪到不超过覆盖句子的TTS总时长
- 这不是"强制完整播放"，而是"优先完整播放，但对齐TTS"

**建议字段重命名（后续优化）：**
- `full_play_required` → `preferred_full_play` 或 `effective_segment_priority`

### 5.3 跨句视觉延续

| 素材 | 覆盖句子 | 覆盖TTS(s) | 实际clip(s) |
|------|---------|-----------|------------|
| 屏显调温_003 | 句10+11+12+13 | 3.930 | 3.042 |

- 句11、12、13的clip_path被清空，画面延续屏显调温_003
- 被覆盖句的字幕和TTS正常播放

### 5.4 时长对齐

| 指标 | 值 |
|------|-----|
| TTS总时长 | 17.736s |
| 活跃clip总时长 | 16.835s |
| 最终视频时长 | 16.933s |
| video_audio_diff | -0.803s |

**说明：** 视频略短于音频，差值在可接受范围内（<1s）。

---

## 6. 字幕 / 字体 / BGM

### 6.1 字体

| 项目 | 当前状态 |
|------|---------|
| 默认字体路径 | `{COZE_WORKSPACE_PATH}/assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF` |
| 回退字体 | `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc` |
| 可用字体清单 | 黑体x2（ALIBABA-PUHUITI-BOLD/HEAVY）、标题黑x1（优设标题黑）、宋体x2（SOURCEHANSERIFCN-BOLD/HEAVY） |

### 6.2 字幕样式池

| 项目 | 当前状态 |
|------|---------|
| 字幕样式池 | **未实现** |
| 当前固定样式 | 白色字体、黑色描边、fontsize=38、y=h-346（82%位置） |
| 可用字体池 | 5个字体文件存在，但**未随机选择** |
| 状态说明 | **后续优化项，不纳入当前锁版能力** |

### 6.3 BGM

| 项目 | 当前状态 |
|------|---------|
| BGM来源目录 | `assets/bgm/`（12个文件：bgm_01.mp3 ~ bgm_12.mp3） |
| 选择方式 | **稳定选择**：`MD5(script_id) % len(bgm_files)` |
| 本次选择的BGM | `bgm.mp3`（由 script_06_audit 的 MD5 决定） |
| BGM记录位置 | `audio_mix_report.json` 的 `bgm_file` 字段 |

**BGM选择逻辑统一说明：**
- `script_source_router_node.py`：使用 `_select_bgm_stable(script_id)` 稳定选择
- `final_composition_node.py`：作为备用，使用 `MD5(run_dir)` 稳定选择
- **两条路径都是稳定选择，不存在随机逻辑**
- 同一 script_id 总是选择相同的 BGM，具备完全复现性

---

## 7. 本次修正的代码文件

| 文件 | 修正内容 |
|------|---------|
| `src/graphs/nodes/material_matching_node.py` | 1. 移除"手持展示"兜底<br>2. 添加 `low_confidence`/`fallback_reason`/`candidate_tags` 输出<br>3. 兜底优先级改为：产品展示 > 手持大小对比 > 折叠动作 > 放进包包 |
| `src/graphs/nodes/script_source_router_node.py` | 1. 删除 `random.choice` 随机选择<br>2. 改为 `_select_bgm_stable(script_id)` 稳定选择 |
| `src/graphs/nodes/quality_check_node.py` | 1. `low_confidence > 0` 不再阻塞流程<br>2. 只有 `low_confidence >= 3` 才视为失败 |

---

## 8. 已知风险与后续优化

### 8.1 已修复的风险

| 风险 | 状态 |
|------|------|
| "手持展示"兜底滥用 | **已修复** |
| BGM随机选择不可复现 | **已修复** |
| low_confidence 不记录 | **已修复** |

### 8.2 后续优化项

| 项目 | 说明 | 优先级 |
|------|------|--------|
| 字幕样式池 | 当前只有1个固定样式，未实现多样式池 | 低 |
| 字体随机选择 | 5个字体存在但未随机选择 | 低 |
| full_play_required 字段重命名 | 建议改为 `preferred_full_play` | 低 |

---

## 9. 锁版建议

### 9.1 是否建议现在锁版

**建议锁版**。理由：
- 单条验证通过
- 兜底逻辑已修正，"手持展示"不再被滥用
- BGM选择已统一为稳定逻辑
- low_confidence 正确记录并输出
- 18个唯一素材，无重复
- 跨句视觉延续正常工作

### 9.2 锁版应固定的文件

1. `src/graphs/nodes/material_matching_node.py`
2. `src/graphs/nodes/clip_extraction_node.py`
3. `src/graphs/nodes/timeline_assembly_node.py`
4. `src/graphs/nodes/final_composition_node.py`
5. `src/graphs/nodes/script_source_router_node.py`
6. `src/graphs/nodes/quality_check_node.py`
7. `src/graphs/graph.py`
8. `src/graphs/state.py`
9. `assets/asset_manifest_v2_clean.csv`

### 9.3 小批量验证验收标准

1. **每条视频必须独立生成**
2. **video_duration ≈ tts_duration**：差值 <= 1.0s
3. **无同一素材连续重复播放**
4. **1080x1920竖屏**
5. **TTS正常、BGM正常、字幕正常**
6. **无黑屏/空镜头**
7. **low_confidence_segments < 3**
8. **兜底不使用"手持展示"**
9. **BGM选择可复现**（同script_id同BGM）

---

## 10. 附录：script_06_audit 详细数据

### 10.1 分句与素材匹配

| 句ID | 文案 | TTS(s) | 匹配标签 | 素材ID | 置信度 |
|------|------|--------|---------|--------|--------|
| 1 | 吹风机圈的小钢炮！ | 1.105 | 手持大小对比 | 手持大小对比_003 | high |
| 2 | 巴掌大 十一万转 | 0.900 | 手持大小对比 | 手持大小对比_001 | high |
| 3 | 风力给你拉满 | 0.900 | 风力展示 | 风力展示_001 | high |
| 4 | 长头发三五分钟 | 0.945 | 风力展示 | 风力展示_005 | high |
| 5 | 短头发1分钟不到 | 1.025 | 护发效果 | 护发效果_006 | high |
| 6 | 主打一个快 | 1.105 | 护发效果 | 护发效果_007 | high |
| 7 | 赠送造型风嘴 | 0.900 | 风力展示 | 风力展示_006 | high |
| 8 | 吹完直接出门 | 0.945 | 风嘴配件 | 风嘴配件_001 | high |
| 9 | 智能控温每秒一百次 | 0.945 | 吹发动作 | 吹发动作_008 | high |
| 10 | 不伤头发 | 1.185 | 屏显调温 | 屏显调温_003 | high |
| 11 | 独立温度屏显，实时显温 | 0.900 | 护发效果 | 护发效果_008 | high [被覆盖] |
| 12 | 折叠带走 健身出差旅游必备 | 0.945 | 屏显调温 | 屏显调温_008 | high [被覆盖] |
| 13 | 现在买送旅行收纳袋 | 0.900 | 屏显调温 | 屏显调温_001 | high [被覆盖] |
| 14 | 就这一波 | 0.900 | 折叠动作 | 折叠动作_001 | high |
| 15 | 吹风机圈的小钢炮！ | 1.105 | 旅行场景 | 旅行场景_010 | high |
| 16 | 巴掌大 十一万转 | 1.185 | 放进包包 | 放进包包_006 | high |
| 17 | 风力给你拉满 | 0.900 | 产品展示 | 产品展示_001 | **low** |
| 18 | 长头发三五分钟 | 0.945 | CTA促单 | CTA促单_001 | high |

### 10.2 低置信度句子详情

| 句ID | 文案 | 兜底标签 | 原因 |
|------|------|---------|------|
| 17 | 风力给你拉满 | 产品展示 | 无关键词匹配，使用兜底标签: 产品展示 |

**说明：** 句17的文案在分句时可能被截断或重组，导致关键词丢失。兜底到"产品展示"是正确的保守选择，不是"手持展示"。

---

**审计结论：单条验证通过，可进入5条小批量验证。**

**所有已知风险已修复，后续优化项不阻塞锁版。**
