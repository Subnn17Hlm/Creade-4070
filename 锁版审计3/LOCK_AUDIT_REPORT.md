# 锁版前审计报告 v3.0

**审计日期**: 2026-07-16  
**稳定 run_dir**: `runs/script_07_final`  
**审计结论**: ✅ 建议进入5条小批量验证

---

## 1. 当前稳定版本信息

| 项目 | 值 |
|------|-----|
| 最终验证 run_dir | `runs/script_07_final/` |
| 最终视频路径 | `/workspace/projects/runs/script_07_final/final.mp4` |
| 视频时长 | **19.000s** |
| TTS总时长 | **19.104s** |
| video_audio_diff | **-0.104s** ✅（≤0.5s） |
| TTS | ✅ 正常 |
| BGM | ✅ 正常（已混入） |
| 字幕 | ✅ 正常（18句全部烧录） |
| 字体 | ✅ 项目字体 `ALIBABA-PUHUITI-BOLD.TTF` |
| 分辨率 | ✅ 1080x1920 竖屏 |

---

## 2. 核心数据一致性

| 指标 | 值 | 状态 |
|------|-----|------|
| 分句数量 | 18 | ✅ |
| 字幕数量 | 18 | ✅ |
| selected_assets 数量 | 18 | ✅ |
| 唯一素材数量 | 18 | ✅ 无重复 |
| 高置信度匹配 | 17 | ✅ |
| 低置信度匹配 | 1 | ✅ 已标记 |
| 是否触发兜底 | 是（句17） | ✅ 兜底到"产品展示" |
| 兜底是否包含"手持展示" | **否** | ✅ 已移除 |

**数据一致性结论**: 18句 = 18字幕 = 18素材 = 18唯一素材 ✅

---

## 3. 语义标签匹配逻辑

### 3.1 标签生成方式
- **纯代码规则生成**，不调用大模型
- 核心函数: `_generate_sentence_tag_mapping()` in `material_matching_node.py`
- 关键词映射: `_KEYWORD_TO_TAG` 字典（约50+关键词→标签映射）

### 3.2 兜底逻辑（已修正）
| 场景 | 兜底标签 | 说明 |
|------|---------|------|
| 短句无匹配 | 产品展示 → 手持大小对比 → 折叠动作 → 放进包包 | **不再包含"手持展示"** |
| 长句无匹配 | 旅行场景 → 痛点共鸣 → 产品展示 → 折叠动作 | **不再包含"手持展示"** |
| 兜底输出 | `low_confidence=true` + `fallback_reason` + `candidate_tags` | 写入质量报告 |

### 3.3 已知风险
- **⚠️ 手持展示兜底已移除**: 当前兜底标签为"产品展示"，不再使用"手持展示"
- **⚠️ 标签不匹配**: `sentence_tag_mapping.json` 的要求标签与 `selected_assets.json` 的选中素材标签存在4处不一致（见附录）

---

## 4. 素材匹配逻辑

| 问题 | 回答 |
|------|------|
| 使用的CSV文件 | `assets/asset_manifest_v2_clean.csv` |
| 是否只用此CSV | ✅ 是 |
| 是否自动生成素材标签 | ❌ 不会，严格使用CSV的 `primary_scene_tag` |
| description为空时 | 代码支持description辅助匹配，但CSV中description为空时不会伪称生效 |

---

## 5. 素材裁剪与时间线逻辑

### 5.1 effective_start
- ✅ 支持。`MATERIAL_EFFECTIVE_SEGMENT_RULES` 配置了每个素材的effective_start
- 示例: 手持大小对比_003 的 effective_start=1.5s（跳过"手拿瓶装水"铺垫段）

### 5.2 full_play_required / effective_segment
| 素材 | source_duration | 实际使用 | 逻辑说明 |
|------|----------------|---------|---------|
| 屏显调温_003 | 3.040s | 3.040s | 跨句覆盖句11+12，实际播放3.04s |
| 屏显调温_009 | 2.493s | 2.493s | 完整使用 |

**逻辑说明**: 
- `full_play_required=true` 的素材会尝试完整播放
- 通过**跨句视觉延续**覆盖相邻句的视觉区间
- 被覆盖句的 `clip_path` 设为空，不参与concat
- 如果素材时长 > 覆盖句总TTS，会裁剪到匹配
- 如果素材时长 < 覆盖句总TTS，使用素材实际时长

### 5.3 时长对齐
| 指标 | 值 |
|------|-----|
| 活跃clip总时长 | 19.000s |
| TTS总时长 | 19.104s |
| 差值 | -0.104s ✅ |

---

## 6. 字幕 / 字体 / BGM

### 6.1 字幕样式池
| 项目 | 状态 |
|------|------|
| 字幕样式池 | **⚠️ 未实现，后续优化项** |
| 当前固定样式 | 白色字体、黑色描边、fontsize=38、y=h-346（82%位置） |
| 可用字体池 | 5个字体文件存在，但**未随机选择** |

**结论**: 字幕样式池和字体随机选择属于后续优化，不纳入当前锁版能力。

### 6.2 字体配置
| 项目 | 值 |
|------|-----|
| 默认字体路径 | `{COZE_WORKSPACE_PATH}/assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF` |
| 回退字体 | `/usr/share/fonts/truetype/wqy/wqy-microhei.ttc` |

**可用字体文件清单**:
```
assets/Fonts/黑体/ALIBABA-PUHUITI-BOLD.TTF
assets/Fonts/黑体/ALIBABA-PUHUITI-HEAVY.TTF
assets/Fonts/标题黑/优设标题黑.TTF
assets/Fonts/宋体/SOURCEHANSERIFCN-BOLD.OTF
assets/Fonts/宋体/SOURCEHANSERIFCN-HEAVY.OTF
```

### 6.3 BGM选择逻辑（已修正）
| 项目 | 值 |
|------|-----|
| BGM来源目录 | `assets/bgm/`（12个文件：bgm_01.mp3 ~ bgm_12.mp3） |
| 选择规则 | **稳定选择**: `MD5(script_id) % len(bgm_files)` |
| script_07_final 选择的BGM | `bgm_07.mp3` |
| 是否记录到audio_mix_report.json | ✅ 是 |

**修正说明**:
- 已删除 `script_source_router_node.py` 中的 `random.choice()` 随机选择
- 统一使用 `_select_bgm_stable(script_id)` 稳定选择
- 同一 script_id 每次生成选择相同的 BGM，具备可复现性

---

## 7. 已知风险

| 风险 | 说明 | 建议 |
|------|------|------|
| ⚠️ 标签不匹配 | `sentence_tag_mapping` 与 `selected_assets` 存在4处不一致 | 后续优化：统一标签来源 |
| ⚠️ 字幕样式池未实现 | 当前只有固定样式，无多样式池 | 后续优化：实现样式池 |
| ⚠️ 字体未随机选择 | 5个字体存在，但每次都用默认字体 | 后续优化：实现字体随机选择 |

---

## 8. 锁版建议

### 是否建议锁版
**✅ 建议进入5条小批量验证**

### 锁版应固定的文件
1. `src/graphs/nodes/material_matching_node.py` — 标签匹配+素材选择
2. `src/graphs/nodes/clip_extraction_node.py` — 素材裁剪（effective_start/full_play）
3. `src/graphs/nodes/timeline_assembly_node.py` — 跨句视觉延续
4. `src/graphs/nodes/final_composition_node.py` — 合成（字体/BGM/concat/字幕）
5. `src/graphs/nodes/script_source_router_node.py` — BGM稳定选择
6. `src/graphs/nodes/quality_check_node.py` — 质量检查（low_confidence阈值）
7. `src/graphs/graph.py` — 图编排
8. `src/graphs/state.py` — 状态定义
9. `assets/asset_manifest_v2_clean.csv` — 素材清单

### 小批量验证验收标准
1. **每条视频必须独立生成**：不复用其他视频的素材/时间线/缓存
2. **video_audio_diff ≤ 0.5s**
3. **无同一素材连续重复播放**
4. **1080x1920竖屏**
5. **TTS正常、BGM正常、字幕正常**
6. **无黑屏/空镜头**
7. **无统一缩放因子**
8. **低于1.0s镜头不连续出现**
9. **每条视频的素材组合应有差异**
10. **low_confidence_segments < 3**

---

## 附录：script_07_final 详细数据

### A.1 分句与标签映射

| 句ID | 文案 | 要求标签 | 选中素材 | 选中素材标签 | 置信度 |
|------|------|---------|---------|-------------|--------|
| 1 | 吹风机圈的小钢炮 | 手持大小对比 | 手持大小对比_003 | 手持大小对比 | high ✅ |
| 2 | 巴掌大 | 手持大小对比 | 手持大小对比_001 | 手持大小对比 | high ✅ |
| 3 | 十一万转 | 风力展示 | 风力展示_001 | 风力展示 | high ✅ |
| 4 | 风力给你拉满 | 风力展示 | 风力展示_005 | 风力展示 | high ✅ |
| 5 | 长头发三五分钟 | 吹发动作 | 护发效果_006 | 护发效果 | high ⚠️ |
| 6 | 短头发1分钟不到 | 吹发动作 | 护发效果_007 | 护发效果 | high ⚠️ |
| 7 | 主打一个快 | 风力展示 | 风力展示_006 | 风力展示 | high ✅ |
| 8 | 赠送造型风嘴 | 赠品展示 | 风嘴配件_001 | 风嘴配件 | high ⚠️ |
| 9 | 吹完直接出门 | 吹发动作 | 吹发动作_008 | 吹发动作 | high ✅ |
| 10 | 智能控温每秒一百次 | 屏显调温 | 屏显调温_003 | 屏显调温 | high ✅ |
| 11 | 不伤头发 | 护发效果 | 护发效果_008 | 护发效果 | high ✅ |
| 12 | 独立温度屏显 | 屏显调温 | 屏显调温_008 | 屏显调温 | high ✅ |
| 13 | 实时显温 | 屏显调温 | 屏显调温_001 | 屏显调温 | high ✅ |
| 14 | 折叠带走 | 折叠动作 | 折叠动作_001 | 折叠动作 | high ✅ |
| 15 | 健身出差旅游必备 | 旅行场景 | 旅行场景_010 | 旅行场景 | high ✅ |
| 16 | 现在买送旅行收纳袋 | 旅行场景 | 放进包包_006 | 放进包包 | high ⚠️ |
| 17 | 就这一波 | 产品展示 | 产品展示_001 | 产品展示 | low [LOW] ✅ |
| 18 | 闭眼冲就完了 | CTA促单 | CTA促单_001 | CTA促单 | high ✅ |

**标签不匹配说明**: 
- 句5/6: "长头发/短头发" 关键词映射到"吹发动作"，但素材选择时语义回落到"护发效果"
- 句8: "赠送造型风嘴" 关键词映射到"赠品展示"，但素材选择时精确匹配到"风嘴配件"
- 句16: "旅行收纳袋" 关键词映射到"旅行场景"，但素材选择时语义回落到"放进包包"

这些不匹配是设计层面的问题：`selected_primary_scene_tag` 是**选中素材的标签**，不是**要求的标签**。后续优化可统一标签来源。

### A.2 跨句视觉延续

| 句ID | 素材 | TTS时长 | clip时长 | 状态 |
|------|------|---------|----------|------|
| 10 | 屏显调温_003 | 1.364s | 3.040s | [FULL_PLAY 覆盖句11,12] |
| 11 | 护发效果_008 | 0.900s | - | [被句10覆盖] |
| 12 | 屏显调温_008 | 1.028s | - | [被句10覆盖] |

### A.3 effective_start 生效情况

| 素材 | effective_start | clip_start | 说明 |
|------|----------------|------------|------|
| 手持大小对比_003 | 1.5s | 1.5s | ✅ 跳过"手拿瓶装水"铺垫段 |

---

**报告生成时间**: 2026-07-16  
**审计版本**: v3.0  
**审计状态**: ✅ 建议进入5条小批量验证
