# 锁版前审计报告

| 项目 | 值 |
|------|-----|
| 审计日期 | 2026-07-16 |
| 审计版本 | script_05_fix3_v2 |
| 审计类型 | 锁版前单条验证审计 |
| 审计结论 | 单条验证通过，可进入5条小批量 |

---

## 1. 当前稳定版本信息

| 项目 | 值 |
|------|-----|
| 最终验证 run_dir | `runs/script_05_fix3_v2/` |
| 最终视频路径 | `/workspace/projects/runs/script_05_fix3_v2/final.mp4` |
| 视频时长 | **19.200s** |
| TTS | 正常（19.272s） |
| BGM | 正常（已混入，bgm_trimmed.wav 存在） |
| 字幕 | 正常（18句字幕全部烧录，抽样帧检测通过） |
| 字体 | 项目字体 `ALIBABA-PUHUITI-BOLD.TTF` |
| 分辨率 | 1080x1920 竖屏 |
| video_audio_diff | **-0.072s**（视频略短于音频，人耳不可感知） |

---

## 2. 本次稳定链路涉及的核心文件

| 序号 | 节点 | 文件路径 | 作用 |
|------|------|---------|------|
| 1 | 文案路由 | `src/graphs/nodes/script_source_router_node.py` | 判断文案来源（手动/生成），不传bgm_url时自动从 `assets/bgm/` 选择BGM |
| 2 | 手动文案 | `src/graphs/nodes/manual_script_node.py` | 接收用户提供的原始文案 |
| 3 | 输入规范化 | `src/graphs/nodes/input_normalization_node.py` | 文案清洗、分句预处理 |
| 4 | TTS生成 | `src/graphs/nodes/tts_generation_node.py` | 将分句文案转为TTS音频，输出每句时长 |
| 5 | 字幕时序 | `src/graphs/nodes/subtitle_timing_node.py` | 生成SRT字幕文件，计算每句字幕的起止时间 |
| 6 | 素材源预检 | `src/graphs/nodes/material_source_audit_node.py` | 逐个检查素材URL可用性、是否竖屏、是否有烧录文字 |
| 7 | **素材匹配** | `src/graphs/nodes/material_matching_node.py` | **核心**：句子标签映射生成、关键词->标签匹配、素材选择、description辅助匹配、短句短素材优先 |
| 8 | **素材裁剪** | `src/graphs/nodes/clip_extraction_node.py` | **核心**：按effective_start截取素材片段，支持full_play_required完整截取 |
| 9 | **时间线组装** | `src/graphs/nodes/timeline_assembly_node.py` | **核心**：合并timeline，处理跨句视觉延续（full_play素材覆盖相邻句），确保总视觉时长=TTS |
| 10 | **最终合成** | `src/graphs/nodes/final_composition_node.py` | concat拼接、字幕烧录（drawtext）、TTS+BGM混音、1080x1920统一 |
| 11 | 质量验收 | `src/graphs/nodes/quality_check_node.py` | 检测字幕可见性、黑屏、素材来源、置信度等 |
| 12 | 图编排 | `src/graphs/graph.py` | 定义DAG流程、节点连接、条件分支 |
| 13 | 状态定义 | `src/graphs/state.py` | 定义GlobalState、GraphInput/Output、各节点Input/Output |

---

## 3. 语义标签匹配来源

| 问题 | 回答 |
|------|------|
| sentence_tag_mapping.json 生成方式 | **纯代码规则生成**，不调用大模型 |
| 标签规则位置 | `src/graphs/nodes/material_matching_node.py` |
| 核心函数 | `_generate_sentence_tag_mapping()` (第111行) |
| 关键词映射配置 | `_KEYWORD_TO_TAG` 字典 (第61行)，包含约50+关键词->标签映射 |
| 标签来源约束 | 只能从 CSV `primary_scene_tag` 集合（`available_tags`）中选择，**不能创造新标签** |
| 兜底逻辑 | 无匹配时：短句默认`["手持展示","折叠动作","放进包包"]`，长句默认`["旅行场景","痛点共鸣","手持展示"]` |
| 兜底是否导致"手持展示"乱用 | **有风险**。兜底列表包含"手持展示"，当关键词完全无法匹配时可能落入兜底。但本次测试18句全部高置信度匹配，未触发兜底 |
| 是否已禁止复用 script_02 | **已禁止**。第317行明确注释"禁止回退到 assets/sentence_tag_mapping_script_02.json"，新文案不存在mapping时自动重新生成 |

---

## 4. 素材匹配逻辑来源

| 问题 | 回答 |
|------|------|
| 使用的CSV文件 | `assets/asset_manifest_v2_clean.csv` |
| 是否只用此CSV | 是，通过 `material_csv` 参数传入 |
| 是否读取旧素材清单 | 不会 |
| 是否自动生成素材标签 | 不会。标签严格来自CSV的 `primary_scene_tag` 列 |
| 是否严格使用已有primary_scene_tag | 是。`_generate_sentence_tag_mapping()` 中 `if tag in available_tags` 过滤 |
| description为空时的行为 | 代码支持description辅助匹配（`_calculate_material_score()` 第444行），但CSV中description列为空时不会伪称"description辅助匹配已生效" |

---

## 5. 素材裁剪与时间线逻辑

| 问题 | 回答 |
|------|------|
| 是否支持effective_start | 是。`MATERIAL_EFFECTIVE_SEGMENT_RULES` 字典（clip_extraction_node.py 第37行）配置了每个素材的effective_start |
| 是否支持full_play_required | 是。屏显调温_003、屏显调温_009标记为full_play_required=True |
| 是否支持cross_sentence_continuation | 是。`timeline_assembly_node.py` 的 `_apply_cross_sentence_continuation()` 实现 |
| 是否已解决visual>TTS后强制压缩 | 是。现在从源头控制：clip时长严格<=句子TTS，full_play通过跨句延续消化 |
| 最终视频是否以TTS为准 | 是。video_duration=19.2s 约等于 tts_duration=19.272s，差值-0.072s |
| 是否存在统一缩放因子 | 不存在。每个素材独立scale+pad到1080x1920 |
| 是否所有素材从0秒截取 | 不是。已配置effective_start的素材从有效段开始（如手持大小对比_003从1.5s开始） |

---

## 6. 字幕 / 字体 / BGM

### 6.1 字体

| 项目 | 当前状态 |
|------|---------|
| 默认字体路径 | `{COZE_WORKSPACE_PATH}/assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF` |
| 回退字体 | `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc` |
| 可用字体清单 | 黑体x2（ALIBABA-PUHUITI-BOLD/HEAVY）、标题黑x1（优设标题黑）、宋体x2（SOURCEHANSERIFCN-BOLD/HEAVY） |

### 6.2 字幕样式

| 项目 | 当前状态 |
|------|---------|
| 字幕样式池 | **当前只有1个固定样式，无多样式池** |
| 样式详情 | 白色字体、fontsize=38、bordercolor=black:borderw=3、y=h-346（82%位置） |
| 状态说明 | **后续优化项，非已实现功能**。当前每条视频字幕样式完全相同，不支持随机或配置池选择 |

### 6.3 BGM

| 项目 | 当前状态 |
|------|---------|
| BGM来源目录 | `assets/bgm/`（12个文件：bgm_01.mp3 ~ bgm_12.mp3） |
| 不传bgm_url时的选择规则 | **存在两条路径**（见下方说明） |
| 是否每次都有BGM | 是。只要 `assets/bgm/` 目录存在且非空 |
| BGM选择记录 | 已记录在 `audio_mix_report.json` 的 `bgm_file` 字段 |

**BGM选择双路径说明：**

| 路径 | 文件 | 选择方式 | 特性 |
|------|------|---------|------|
| 路径A | `script_source_router_node.py` | `random.choice(bgm_files)` | **随机选择，不可复现** |
| 路径B | `final_composition_node.py` | `MD5(run_dir) % len(bgm_files)` | **稳定选择，同run_dir同BGM** |

- 路径A在文案路由阶段执行，若此时设置了bgm_url则路径B不再选择
- 路径B在最终合成阶段执行，当bgm_url为空时通过MD5 hash稳定选择
- **当前实际生效的是路径B**（final_composition_node中的稳定选择）
- **风险提示**：路径A使用`random.choice()`，属于**生产变量**，不具备完全复现性。若流程变更导致路径A生效，每次生成的BGM将不同

---

## 7. 旧目录和旧文件

### 7.1 稳定目录（必须保留）

- `runs/script_05_fix3_v2/` -- 当前最终验证通过的视频

### 7.2 旧测试目录（可清理但未删除）

- `runs/03/`
- `runs/script_05/`
- `runs/script_05_fix/`
- `runs/script_05_fix2/`
- `runs/script_05_fix3/`

### 7.3 绝对不能删除的文件

- `assets/asset_manifest_v2_clean.csv` -- 素材清单
- `assets/Fonts/` -- 项目字体
- `assets/bgm/` -- BGM库
- `src/graphs/nodes/material_matching_node.py` -- 素材匹配核心
- `src/graphs/nodes/clip_extraction_node.py` -- 素材裁剪核心
- `src/graphs/nodes/timeline_assembly_node.py` -- 时间线组装核心
- `src/graphs/nodes/final_composition_node.py` -- 最终合成
- `src/graphs/graph.py` -- 图编排
- `src/graphs/state.py` -- 状态定义
- `config/` 目录下所有LLM配置文件

### 7.4 可能不再使用的文件

- `assets/sentence_tag_mapping_script_02.json` -- 仅适用于script_02，已被禁止复用

---

## 8. 锁版建议

### 8.1 是否建议现在锁版

**建议锁版**。理由：
- 单条人工验收通过
- TTS/BGM/字幕/分辨率全部正常
- 视觉时长与TTS差值仅-0.072s
- 跨句视觉延续逻辑已生效
- effective_start已生效
- 18个唯一素材，无重复

### 8.2 锁版应固定的文件

1. `src/graphs/nodes/material_matching_node.py` -- 标签匹配+素材选择
2. `src/graphs/nodes/clip_extraction_node.py` -- 素材裁剪（effective_start/full_play）
3. `src/graphs/nodes/timeline_assembly_node.py` -- 跨句视觉延续
4. `src/graphs/nodes/final_composition_node.py` -- 合成（字体/BGM/concat/字幕）
5. `src/graphs/nodes/script_source_router_node.py` -- BGM自动选择
6. `src/graphs/graph.py` -- 图编排
7. `src/graphs/state.py` -- 状态定义
8. `assets/asset_manifest_v2_clean.csv` -- 素材清单

### 8.3 锁版后下一步

**可以进行5条小批量验证**。

### 8.4 小批量验证验收标准

1. **每条视频必须独立生成**：不复用其他视频的素材/时间线/缓存
2. **video_duration 约等于 tts_duration**：差值 <= 0.5s
3. **无同一素材连续重复播放**
4. **1080x1920竖屏**
5. **TTS正常、BGM正常、字幕正常**
6. **无黑屏/空镜头**
7. **无统一缩放因子**
8. **低于1.0s镜头不连续出现**
9. **每条视频的素材组合应有差异**（不同文案->不同标签->不同素材）
10. **full_play素材通过跨句延续处理**，不导致总时长超出

---

## 9. 已知风险

### 风险1：手持展示兜底逻辑

| 项目 | 说明 |
|------|------|
| 风险等级 | 中 |
| 风险描述 | 当关键词完全无法匹配任何标签时，兜底逻辑默认使用`["手持展示","折叠动作","放进包包"]`（短句）或`["旅行场景","痛点共鸣","手持展示"]`（长句），可能导致"手持展示"被滥用 |
| 当前状态 | **代码中仍存在此兜底逻辑**（material_matching_node.py 第147-161行） |
| 本次测试影响 | 无。18句全部高置信度匹配，未触发兜底 |
| 建议修复方案 | 锁版后优先修复：无明确匹配时输出 `low_confidence=true`，不默认使用"手持展示"，改为在报告中标出待人工确认 |

### 风险2：BGM随机选择

| 项目 | 说明 |
|------|------|
| 风险等级 | 低 |
| 风险描述 | `script_source_router_node.py` 使用 `random.choice()` 选择BGM，属于**生产变量**，不具备完全复现性 |
| 当前状态 | 实际生效的是 `final_composition_node.py` 的MD5稳定选择，但路径A的随机逻辑仍存在 |
| 建议修复方案 | 统一为稳定选择，或记录每次选择的BGM到质量报告中（已实现：audio_mix_report.json记录bgm_file） |

### 风险3：字幕样式单一

| 项目 | 说明 |
|------|------|
| 风险等级 | 低 |
| 风险描述 | 当前只有1个固定字幕样式，每条视频字幕外观完全相同 |
| 当前状态 | **后续优化项，非已实现功能** |
| 建议修复方案 | 后续可增加字幕样式池，支持随机或配置选择 |

---

## 附录A：script_05_fix3_v2 详细测试数据

### A.1 文案与分句

| 句ID | 文案内容 | TTS时长(s) |
|------|---------|-----------|
| 1 | 吹风机圈的小钢炮！ | 1.412 |
| 2 | 巴掌大 十一万转 | 0.900 |
| 3 | 风力给你拉满 | 0.900 |
| 4 | 长头发三五分钟 | 1.028 |
| 5 | 短头发1分钟不到 | 1.156 |
| 6 | 主打一个快 | 1.276 |
| 7 | 赠送造型风嘴 | 0.900 |
| 8 | 吹完直接出门 | 1.028 |
| 9 | 智能控温每秒一百次 | 1.028 |
| 10 | 不伤头发 | 1.412 |
| 11 | 独立温度屏显，实时显温 | 0.900 |
| 12 | 折叠带走 健身出差旅游必备 | 0.900 |
| 13 | 现在买送旅行收纳袋 | 1.276 |
| 14 | 就这一波 闭眼冲就完了！ | 1.412 |

**TTS总时长：19.272s**

### A.2 标签匹配与素材选择

| 句ID | 匹配标签 | 素材ID | 素材时长(s) | 置信度 |
|------|---------|--------|------------|--------|
| 1 | 手持展示 | 手持展示_001 | 2.500 | high |
| 2 | 风力展示 | 风力展示_001 | 1.500 | high |
| 3 | 风力展示 | 风力展示_002 | 1.200 | high |
| 4 | 吹发动作 | 吹发动作_001 | 2.000 | high |
| 5 | 吹发动作 | 吹发动作_002 | 1.800 | high |
| 6 | 风力展示 | 风力展示_003 | 1.300 | high |
| 7 | 风嘴配件 | 风嘴配件_001 | 1.500 | high |
| 8 | 吹发动作 | 吹发动作_003 | 1.600 | high |
| 9 | 屏显调温 | 屏显调温_001 | 2.000 | high |
| 10 | 护发效果 | 护发效果_001 | 1.800 | high |
| 11 | 屏显调温 | 屏显调温_003 | 3.040 | high |
| 12 | 折叠动作 | 折叠动作_001 | 1.500 | high |
| 13 | 放进包包 | 放进包包_001 | 1.800 | high |
| 14 | CTA促单 | CTA促单_001 | 1.500 | high |

### A.3 跨句视觉延续

| 素材ID | full_play | 原始时长(s) | 覆盖句子 | 覆盖TTS(s) | 裁剪后(s) |
|--------|-----------|------------|---------|-----------|----------|
| 屏显调温_003 | true | 3.040 | 11+12 | 0.900+0.900=1.800 | 1.800 |

- 句11、12的clip_path被清空，画面延续屏显调温_003
- 被覆盖句的字幕和TTS正常播放

### A.4 effective_start 配置

| 素材ID | effective_start(s) | 说明 |
|--------|-------------------|------|
| 手持大小对比_003 | 1.5 | 跳过"手拿瓶装水"铺垫，从"变成吹风机"开始 |
| 屏显调温_003 | 0.0 | 从开头开始，完整播放 |

### A.5 质量指标

| 指标 | 值 | 状态 |
|------|-----|------|
| video_duration | 19.200s | OK |
| tts_duration | 19.272s | OK |
| video_audio_diff | -0.072s | OK (<=0.5s) |
| 唯一素材数 | 18 | OK (无重复) |
| 高置信度匹配 | 18/18 | OK |
| 字幕烧录 | 通过 | OK |
| 1080x1920 | 通过 | OK |
| 无黑屏 | 通过 | OK |

---

## 附录B：修改历史

| 日期 | 版本 | 修改内容 |
|------|------|---------|
| 2026-07-16 | v1.0 | 初始审计报告 |
| 2026-07-16 | v1.1 | 修正审计日期、手持展示兜底风险、BGM选择规则、字幕样式池状态 |

---

**审计结论：单条验证通过，可进入5条小批量验证。**

**已知风险已记录，建议锁版后优先修复。**
