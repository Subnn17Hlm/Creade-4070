# 锁版前审计报告

> 生成时间：2026-07-16  
> 修订时间：2026-07-16（修正审计日期、兜底风险、BGM规则、字幕样式池状态）

---

## 1. 当前稳定版本信息

| 项目 | 值 |
|------|-----|
| 最终验证 run_dir | `runs/script_05_fix3_v2/` |
| 最终视频路径 | `/workspace/projects/runs/script_05_fix3_v2/final.mp4` |
| 视频时长 | **19.200s** |
| TTS | ✅ 正常（19.272s） |
| BGM | ✅ 正常（已混入，bgm_trimmed.wav 存在） |
| 字幕 | ✅ 正常（18句字幕全部烧录，抽样帧检测通过） |
| 字体 | ✅ 项目字体 `ALIBABA-PUHUITI-BOLD.TTF` |
| 分辨率 | ✅ 1080x1920 竖屏 |
| video_audio_diff | **-0.072s**（视频略短于音频，人耳不可感知） |
| 唯一素材数 | 18个，无重复 |
| 语义匹配置信度 | 18/18 高置信度 |

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
| 7 | **素材匹配** | `src/graphs/nodes/material_matching_node.py` | **核心**：句子标签映射生成、关键词→标签匹配、素材选择、description辅助匹配、短句短素材优先 |
| 8 | **素材裁剪** | `src/graphs/nodes/clip_extraction_node.py` | **核心**：按effective_start截取素材片段，支持full_play_required完整截取 |
| 9 | **时间线组装** | `src/graphs/nodes/timeline_assembly_node.py` | **核心**：合并timeline，处理跨句视觉延续（full_play素材覆盖相邻句），确保总视觉时长=TTS |
| 10 | **最终合成** | `src/graphs/nodes/final_composition_node.py` | concat拼接、字幕烧录（drawtext）、TTS+BGM混音、1080x1920统一 |
| 11 | 质量验收 | `src/graphs/nodes/quality_check_node.py` | 检测字幕可见性、黑屏、素材来源、置信度等 |
| 12 | 图编排 | `src/graphs/graph.py` | 定义DAG流程、节点连接、条件分支 |
| 13 | 状态定义 | `src/graphs/state.py` | 定义GlobalState、GraphInput/Output、各节点Input/Output |

### 生成流程图

```
script_source_router
  ├── [生成文案] → generate_script → input_normalization
  └── [手动文案] → manual_script   → input_normalization
                                          ↓
                                    tts_generation
                                          ↓
                                    subtitle_timing
                                          ↓
                                  material_source_audit
                                          ↓
                                   material_matching
                                          ↓
                                    clip_extraction
                                          ↓
                                   timeline_assembly
                                          ↓
                                    final_composition
                                          ↓
                                     quality_check
                                          ↓
                                         END
```

---

## 3. 语义标签匹配来源

| 问题 | 回答 |
|------|------|
| sentence_tag_mapping.json 生成方式 | **纯代码规则生成**，不调用大模型 |
| 标签规则位置 | `src/graphs/nodes/material_matching_node.py` |
| 核心函数 | `_generate_sentence_tag_mapping()` (第111行) |
| 关键词映射配置 | `_KEYWORD_TO_TAG` 字典 (第61行)，包含约50+关键词→标签映射 |
| 标签来源约束 | 只能从 CSV `primary_scene_tag` 集合（`available_tags`）中选择，**不能创造新标签** |
| 兜底逻辑 | 无匹配时：短句默认`["手持展示","折叠动作","放进包包"]`，长句默认`["旅行场景","痛点共鸣","手持展示"]` |
| 兜底是否导致"手持展示"乱用 | ⚠️ **锁版风险**。兜底列表包含"手持展示"，当关键词完全无法匹配时可能落入兜底。本次测试18句全部高置信度匹配，未触发兜底，但**新文案若包含未覆盖关键词仍可能触发**。建议锁版前或锁版后优先修复：将兜底改为输出 `low_confidence=true` 并在报告中标出，不再默认使用"手持展示" |
| 是否已禁止复用 script_02 | ✅ **已禁止**。第317行明确注释"禁止回退到 assets/sentence_tag_mapping_script_02.json"，新文案不存在mapping时自动重新生成 |

### 关键词映射表（_KEYWORD_TO_TAG 摘要）

| 关键词 | 映射标签 |
|--------|---------|
| 出差/旅行/出行/旅游/健身/必备 | 旅行场景 |
| 行李箱/旅行箱 | 放进行李箱 |
| 折叠/收起/收纳 | 折叠动作 |
| 风嘴/造型嘴 | 风嘴配件 |
| 赠送/送/收纳袋/礼物 | 赠品展示 |
| 十一万转/转速/风力/大风/拉满 | 风力展示 |
| 头发/吹干/吹完/顺滑 | 吹发动作 |
| 不伤/护发/柔顺/光泽 | 护发效果 |
| 控温/屏显/显温/温度 | 屏显调温 |
| 巴掌大/小巧/便携/小钢炮 | 手持大小对比 |
| 快/速度/分钟 | 风力展示 |
| 买/冲/划算/价格 | CTA促单 |
| 放包/装包/放进包包 | 放进包包 |

---

## 4. 素材匹配逻辑来源

| 问题 | 回答 |
|------|------|
| 使用的CSV文件 | `assets/asset_manifest_v2_clean.csv` |
| 是否只用此CSV | ✅ 是，通过 `material_csv` 参数传入 |
| 是否读取旧素材清单 | ❌ 不会 |
| 是否自动生成素材标签 | ❌ 不会。标签严格来自CSV的 `primary_scene_tag` 列 |
| 是否严格使用已有primary_scene_tag | ✅ 是。`_generate_sentence_tag_mapping()` 中 `if tag in available_tags` 过滤 |
| description为空时的行为 | 代码支持description辅助匹配（`_calculate_material_score()` 第444行），但CSV中description列为空时不会伪称"description辅助匹配已生效" |

### 素材匹配流程

```
句子文本 → _KEYWORD_TO_TAG关键词匹配 → primary_scene_tag
                                         ↓
                              CSV中按primary_scene_tag精确筛选
                                         ↓
                              enabled=true 的候选素材
                                         ↓
                    _calculate_material_score() 评分排序
                    (duration匹配度 + 关键词重叠 + 未使用优先)
                                         ↓
                              选择得分最高的素材
```

---

## 5. 素材裁剪与时间线逻辑

| 问题 | 回答 |
|------|------|
| 是否支持effective_start | ✅ 是。`MATERIAL_EFFECTIVE_SEGMENT_RULES` 字典（clip_extraction_node.py 第37行）配置了每个素材的effective_start |
| 是否支持full_play_required | ✅ 是。屏显调温_003、屏显调温_009标记为full_play_required=True |
| 是否支持cross_sentence_continuation | ✅ 是。`timeline_assembly_node.py` 的 `_apply_cross_sentence_continuation()` 实现 |
| 是否已解决visual>TTS后强制压缩 | ✅ 是。现在从源头控制：clip时长严格≤句子TTS，full_play通过跨句延续消化 |
| 最终视频是否以TTS为准 | ✅ 是。video_duration=19.2s ≈ tts_duration=19.272s，差值-0.072s |
| 是否存在统一缩放因子 | ❌ 不存在。每个素材独立scale+pad到1080x1920 |
| 是否所有素材从0秒截取 | ❌ 不是。已配置effective_start的素材从有效段开始 |

### effective_start 配置表（MATERIAL_EFFECTIVE_SEGMENT_RULES 摘要）

| 素材ID | effective_start | full_play_required | 说明 |
|--------|----------------|-------------------|------|
| 手持大小对比_003 | 1.5s | - | 手拿瓶装水变吹风机，从变成吹风机后开始 |
| 屏显调温_003 | - | ✅ True | 分屏/完整演示，需尽量全时长使用 |
| 屏显调温_009 | - | ✅ True | 分屏/完整演示，需尽量全时长使用 |
| 屏显调温_001~008 | 0.5s | - | 从有效展示段开始 |
| 风力展示_001~006 | 0.3s | - | 从风力已产生效果位置开始 |
| 护发效果_001~010 | 0.5s | - | 从有效展示段开始 |

### 跨句视觉延续机制

```
句10: 屏显调温_003 (TTS=1.4s, clip=3.0s, full_play=true)
  ↓ 覆盖相邻句
句11: 护发效果_008 (TTS=0.9s, clip_path="" ← 被覆盖)
句12: 屏显调温_008 (TTS=1.0s, clip_path="" ← 被覆盖)

结果：屏显调温_003的clip(3.0s)覆盖句10+11+12的视觉区间
      句11、12的字幕和TTS正常播放，画面延续屏显调温_003
      总视觉时长 = 句10 TTS + 句11 TTS + 句12 TTS = 3.3s
```

---

## 6. 字幕 / 字体 / BGM

### 字幕

| 项目 | 当前状态 |
|------|---------|
| 渲染方式 | ffmpeg drawtext filter链 |
| 字体 | `assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF` |
| 字号 | 38px |
| 颜色 | 白色，黑色描边(borderw=3) |
| 位置 | x居中，y=h-346（约82%高度） |
| 最大行数 | 2行 |
| 面积占比 | 约5% |

### 字体

| 项目 | 值 |
|------|-----|
| 默认字体路径 | `{COZE_WORKSPACE_PATH}/assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF` |
| 回退字体 | `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc` |
| 字幕样式池 | **当前只有1个固定样式**，无多样式池（⚠️ 后续优化项，非已实现功能） |

**可用字体文件清单：**

| 目录 | 文件 |
|------|------|
| assets/Fonts/黑体/ | ALIBABA-PUHUITI-BOLD.TTF |
| assets/Fonts/黑体/ | ALIBABA-PUHUITI-HEAVY.TTF |
| assets/Fonts/标题黑/ | 优设标题黑.TTF |
| assets/Fonts/宋体/ | SOURCEHANSERIFCN-BOLD.OTF |
| assets/Fonts/宋体/ | SOURCEHANSERIFCN-HEAVY.OTF |

### BGM

| 项目 | 值 |
|------|-----|
| BGM来源目录 | `assets/bgm/` |
| 可用BGM数量 | 12个（bgm_01.mp3 ~ bgm_12.mp3） |
| 选择规则 | **两条路径**：① `script_source_router_node.py` 的 `_select_random_bgm()` 使用 `random.choice()` **随机选择**；② `final_composition_node.py` 当 `bgm_url` 为空时使用 `run_dir` 的 MD5 哈希稳定选择 |
| 实际生效路径 | 当 `bgm_url` 为空时，`script_source_router_node` 先随机选一个 BGM 写入 `bgm_url`，后续 `final_composition_node` 使用该 URL。因此**实际 BGM 是随机的** |
| 复现性 | ⚠️ **BGM 是生产变量**，每次生成的 BGM 选择不可完全复现。如需复现，必须显式传入 `bgm_url` 参数 |
| BGM 记录 | ✅ 每次选择的 BGM 文件路径写入 `audio_mix_report.json` 的 `bgm_file` 字段 |
| 不传bgm_url时 | 自动从目录随机选择1个 |
| 是否每次都有BGM | ✅ 是。只要目录存在且非空 |
| 后续优化建议 | 可改为按 `script_id` 稳定选择（如 `hash(script_id) % len(bgm_files)`），确保同一文案每次生成使用相同 BGM |

---

## 7. 旧目录和旧文件

### 稳定目录（必须保留）

- `runs/script_05_fix3_v2/` — 当前最终验证通过的视频

### 旧测试目录（可清理但未删除）

- `runs/03/`
- `runs/script_05/`
- `runs/script_05_fix/`
- `runs/script_05_fix2/`
- `runs/script_05_fix3/`

### 绝对不能删除的文件

| 类别 | 文件 |
|------|------|
| 素材清单 | `assets/asset_manifest_v2_clean.csv` |
| 字体 | `assets/Fonts/` 整个目录 |
| BGM | `assets/bgm/` 整个目录 |
| 素材匹配 | `src/graphs/nodes/material_matching_node.py` |
| 素材裁剪 | `src/graphs/nodes/clip_extraction_node.py` |
| 时间线组装 | `src/graphs/nodes/timeline_assembly_node.py` |
| 最终合成 | `src/graphs/nodes/final_composition_node.py` |
| 图编排 | `src/graphs/graph.py` |
| 状态定义 | `src/graphs/state.py` |
| LLM配置 | `config/` 目录下所有JSON文件 |

### 可能不再使用的文件

- `assets/sentence_tag_mapping_script_02.json` — 仅适用于script_02，已被禁止复用

---

## 8. 锁版建议

### 是否建议现在锁版

**✅ 建议锁版。**

理由：
- 单条人工验收通过
- TTS/BGM/字幕/分辨率全部正常
- 视觉时长与TTS差值仅-0.072s
- 跨句视觉延续逻辑已生效
- effective_start已生效
- 18个唯一素材，无重复
- 18/18高置信度匹配

### ⚠️ 已知风险（锁版后需优先修复）

| 风险项 | 说明 | 建议修复时机 |
|--------|------|-------------|
| 手持展示兜底逻辑 | 当关键词完全无法匹配时，兜底列表包含"手持展示"，可能导致不相关句子被错误分配到手持展示标签。当前测试文案未触发兜底，但新文案可能触发 | 锁版后、小批量前优先修复：将兜底改为输出 `low_confidence=true` 并在报告中标出 |
| BGM随机选择 | `script_source_router_node.py` 使用 `random.choice()` 随机选择BGM，不具备完全复现性 | 锁版后可改为按 `script_id` 稳定选择 |
| 字幕样式单一 | 当前只有1个固定字幕样式，无多样式池 | 后续优化项 |

### 锁版应固定的文件

| 优先级 | 文件 | 说明 |
|--------|------|------|
| P0 | `src/graphs/nodes/material_matching_node.py` | 标签匹配+素材选择 |
| P0 | `src/graphs/nodes/clip_extraction_node.py` | 素材裁剪（effective_start/full_play） |
| P0 | `src/graphs/nodes/timeline_assembly_node.py` | 跨句视觉延续 |
| P0 | `src/graphs/nodes/final_composition_node.py` | 合成（字体/BGM/concat/字幕） |
| P0 | `src/graphs/nodes/script_source_router_node.py` | BGM自动选择 |
| P1 | `src/graphs/graph.py` | 图编排 |
| P1 | `src/graphs/state.py` | 状态定义 |
| P1 | `assets/asset_manifest_v2_clean.csv` | 素材清单 |

### 锁版后下一步

**✅ 可以进行5条小批量验证。**

### 小批量验证验收标准

| 序号 | 标准 | 说明 |
|------|------|------|
| 1 | 每条视频独立生成 | 不复用其他视频的素材/时间线/缓存 |
| 2 | video_duration ≈ tts_duration | 差值 ≤ 0.5s |
| 3 | 无同一素材连续重复播放 | 同一素材不连续出现 |
| 4 | 1080x1920竖屏 | 分辨率统一 |
| 5 | TTS/BGM/字幕正常 | 音频清晰、BGM可闻、字幕可见 |
| 6 | 无黑屏/空镜头 | 全程有画面 |
| 7 | 无统一缩放因子 | 每个素材独立处理 |
| 8 | 低于1.0s镜头不连续出现 | 避免闪烁感 |
| 9 | 素材组合有差异 | 不同文案→不同标签→不同素材 |
| 10 | full_play素材跨句延续 | 不导致总时长超出TTS |

---

## 附录：本次测试视频详细数据

### 文案内容

```
吹风机圈的小钢炮！
巴掌大 十一万转
风力给你拉满
长头发三五分钟
短头发1分钟不到
主打一个快
赠送造型风嘴
吹完直接出门
智能控温每秒一百次
不伤头发
独立温度屏显，实时显温
折叠带走 健身出差旅游必备
现在买送旅行收纳袋
就这一波 闭眼冲就完了！
```

### 每句素材分配

| 句ID | TTS时长 | 素材ID | 素材描述 | clip时长 | 备注 |
|------|---------|--------|---------|---------|------|
| 1 | 1.284s | 手持大小对比_003 | 手拿瓶装水变吹风机 | 1.280s | effective_start=1.5s |
| 2 | 0.900s | 手持大小对比_001 | - | 0.900s | |
| 3 | 0.900s | 风力展示_001 | - | 0.900s | |
| 4 | 1.028s | 风力展示_005 | - | 1.033s | |
| 5 | 1.156s | 护发效果_006 | - | 1.167s | |
| 6 | 1.284s | 护发效果_007 | - | 1.280s | |
| 7 | 0.900s | 风力展示_006 | - | 0.900s | |
| 8 | 1.028s | 风嘴配件_001 | - | 1.033s | |
| 9 | 1.028s | 吹发动作_008 | - | 1.033s | |
| 10 | 1.412s | 屏显调温_003 | - | 3.042s | **FULL_PLAY** 覆盖句11,12 |
| 11 | 0.900s | 护发效果_008 | - | - | 被句10覆盖 |
| 12 | 1.028s | 屏显调温_008 | - | - | 被句10覆盖 |
| 13 | 0.900s | 屏显调温_001 | - | 0.900s | |
| 14 | 0.900s | 折叠动作_001 | - | 0.900s | |
| 15 | 1.284s | 旅行场景_010 | - | 1.280s | |
| 16 | 1.412s | 放进包包_006 | - | 1.410s | |
| 17 | 0.900s | 手持展示_001 | - | 0.900s | |
| 18 | 1.028s | CTA促单_001 | - | 1.033s | |

### 时长校验

| 指标 | 值 |
|------|-----|
| TTS总时长 | 19.272s |
| 活跃clip总时长 | 18.992s |
| 差值 | -0.280s |
| 活跃clip数 | 16/18 |
| 最终视频时长 | 19.200s |
| video_audio_diff | -0.072s |
