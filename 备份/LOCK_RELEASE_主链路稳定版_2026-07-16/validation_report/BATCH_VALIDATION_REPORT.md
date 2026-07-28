# 5条小批量验证报告（修复版2）

**生成时间**: 2026-07-16  
**验证目的**: 验证规则版语义匹配优化 + video_audio_diff修复后的稳定性  
**本轮优化内容**:
1. 扩充口语化/种草关键词（"是不是"、"挖到"、"神器"、"颜值高"、"强悍"、"小东西"、"焊在"、"到手还是"等）
2. 修复跨句视觉延续逻辑：当clip时长不足以覆盖所有被覆盖句子时，回退只覆盖clip能实际覆盖的句子
3. 修复兜底关键词列表同步

---

## 验收标准

| 项目 | 标准 |
|------|------|
| low_confidence | < 3 才通过 |
| 手持展示兜底 | 必须为否 |
| 素材重复 | 不能有高频重复 |
| video_audio_diff | ≤ 0.5s |

---

## 验证结果总览

| 批次 | 状态 | low_confidence | video_audio_diff | 重复素材 | 手持展示兜底 |
|------|------|----------------|------------------|----------|--------------|
| batch_fix2_01 | ✅ PASSED | 2 | 0.000s | 无 | 否 |
| batch_fix2_02 | ✅ PASSED | 2 | -0.000s | 无 | 否 |
| batch_fix2_03 | ✅ PASSED | 0 | 0.000s | 无 | 否 |
| batch_fix2_04 | ✅ PASSED | 2 | 0.000s | 无 | 否 |
| batch_fix2_05 | ✅ PASSED | 0 | 0.000s | 无 | 否 |

**通过率**: 5/5 (100%) ✅

---

## 三轮对比

| 批次 | 第1轮 low_conf | 第2轮 low_conf | 第3轮 low_conf | 第2→3轮 diff |
|------|---------------|---------------|---------------|-------------|
| 01 | 6 | 2 | 2 | 持平 |
| 02 | 4 | 4 | 2 | **-50%** |
| 03 | 3 | 0 | 0 | 持平 |
| 04 | 14 | 3 | 2 | **-33%** |
| 05 | 7 | 0 | 0 | 持平 |
| **总计** | **34** | **9** | **6** | **-33%** |

| 批次 | 第1轮 diff | 第2轮 diff | 第3轮 diff |
|------|-----------|-----------|-----------|
| 01 | - | -0.112s | 0.000s |
| 02 | - | -0.272s | -0.000s |
| 03 | - | 0.000s | 0.000s |
| 04 | - | **-1.064s** | **0.000s** ✅ |
| 05 | - | -0.296s | 0.000s |

---

## 各批次详细信息

### batch_fix2_01 ✅

- **文案**: Creade终于把高性能的风...
- **分句数**: 18
- **唯一素材数**: 18
- **low_confidence**: 2 (< 3 ✅)
- **video_duration**: 22.248s
- **tts_duration**: 22.248s
- **video_audio_diff**: 0.000s (≤ 0.5s ✅)
- **重复素材**: 无 ✅
- **手持展示兜底**: 否 ✅
- **final.mp4**: `runs/batch_fix2_01/final.mp4`
- **final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_16a3e648.mp4?sign=1784232025-1dd0cf1449-0-c38f694e47f1ada488fde8726f1d31d48739ac6b6550ba2bb42b994dfd479466

**low_confidence 句子**:
- 句1: "Creade终于把高性能的风" → 产品展示 (fallback: 品牌名+Creade，无精确关键词)
- 句3: "就是这款" → 产品展示 (fallback: 指示代词，无精确关键词)

---

### batch_fix2_02 ✅

- **文案**: 每次出差旅行...
- **分句数**: 20
- **唯一素材数**: 20
- **low_confidence**: 2 (< 3 ✅) ← 上轮4，本轮2，**改善50%**
- **video_duration**: 26.832s
- **tts_duration**: 26.832s
- **video_audio_diff**: -0.000s (≤ 0.5s ✅)
- **重复素材**: 无 ✅
- **手持展示兜底**: 否 ✅
- **final.mp4**: `runs/batch_fix2_02/final.mp4`
- **final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_c318ff5d.mp4?sign=1784232172-54fa6bc8e7-0-4e651297b5945b8812d7cd9687d404b2e8fe2dff043154bc158cd47b5a024f66

**low_confidence 句子**:
- 句11: "别看它小" → 产品展示 (fallback: 口语化短句，无精确关键词)
- 句14: "几分钟就搞定了" → 产品展示 (fallback: 口语化表达，无精确关键词)

**上轮修复效果**: "是不是都在为"、"挖到"、"出差神器"、"颜值高"、"强悍"、"小东西"、"焊在" 等关键词已正确匹配，从4个low_confidence降至2个。

---

### batch_fix2_03 ✅

- **文案**: 不挑包包 不占空间...
- **分句数**: 15
- **唯一素材数**: 15
- **low_confidence**: 0 (< 3 ✅)
- **video_duration**: 20.856s
- **tts_duration**: 20.856s
- **video_audio_diff**: 0.000s (≤ 0.5s ✅)
- **重复素材**: 无 ✅
- **手持展示兜底**: 否 ✅
- **final.mp4**: `runs/batch_fix2_03/final.mp4`
- **final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_91ef6707.mp4?sign=1784232306-f12ddd4b5e-0-a0ec66ca661de6b4c24959d18ad40e170c1cccb33c7646f63c8e9501d67cba02

**low_confidence 句子**: 无

---

### batch_fix2_04 ✅

- **文案**: 直降399 到手还是这么多...
- **分句数**: 21
- **唯一素材数**: 21
- **low_confidence**: 2 (< 3 ✅) ← 上轮3，本轮2
- **video_duration**: 24.048s
- **tts_duration**: 24.048s
- **video_audio_diff**: 0.000s (≤ 0.5s ✅) ← **上轮-1.064s，本轮0.000s，已修复！**
- **重复素材**: 无 ✅
- **手持展示兜底**: 否 ✅
- **final.mp4**: `runs/batch_fix2_04/final.mp4`
- **final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_cc9336d4.mp4?sign=1784232443-f24a1e2191-0-abd75363b8967c157c3c5c7c1c69dc5e7d3228f153e9c158aad7a7a9cda86f5e

**low_confidence 句子**:
- 句7: "但它的本事" → 产品展示 (fallback: 转折句，无精确关键词)
- 句8: "可不止是'小'" → 产品展示 (fallback: 口语化短句，无精确关键词)

**video_audio_diff修复说明**: 上轮因full_play_required素材跨句延续时，clip时长不足以覆盖所有被覆盖句子，导致视觉总时长比TTS短1.064s。本轮修复了跨句延续逻辑：当clip不够长时，回退只覆盖能实际覆盖的句子，确保视觉总时长=TTS总时长。

---

### batch_fix2_05 ✅

- **文案**: 这么小的吹风机...
- **分句数**: 18
- **唯一素材数**: 18
- **low_confidence**: 0 (< 3 ✅)
- **video_duration**: 24.600s
- **tts_duration**: 24.600s
- **video_audio_diff**: 0.000s (≤ 0.5s ✅)
- **重复素材**: 无 ✅
- **手持展示兜底**: 否 ✅
- **final.mp4**: `runs/batch_fix2_05/final.mp4`
- **final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_6fb0a764.mp4?sign=1784232576-04fd144101-0-9a0188226fa1750e1ad79f3ad75d1434fe53c4cfd1e676138a9bdaec83fadb0b

**low_confidence 句子**: 无

---

## 验收标准检查结果

| 验收项 | 标准 | 01 | 02 | 03 | 04 | 05 |
|--------|------|----|----|----|----|-----|
| low_confidence < 3 | < 3 | ✅ 2 | ✅ 2 | ✅ 0 | ✅ 2 | ✅ 0 |
| 不使用"手持展示"兜底 | 否 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 无素材高频重复 | 无 | ✅ | ✅ | ✅ | ✅ | ✅ |
| video_audio_diff ≤ 0.5s | ≤ 0.5s | ✅ 0.0s | ✅ 0.0s | ✅ 0.0s | ✅ 0.0s | ✅ 0.0s |
| TTS 正常 | 正常 | ✅ | ✅ | ✅ | ✅ | ✅ |
| BGM 正常 | 正常 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 字幕正常烧录 | 正常 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 1080x1920 竖屏 | 是 | ✅ | ✅ | ✅ | ✅ | ✅ |
| 无黑屏/空镜头 | 无 | ✅ | ✅ | ✅ | ✅ | ✅ |

---

## 结论

### 当前状态

- **通过率**: 5/5 (100%) ✅
- **low_confidence 总数**: 6（从34→9→6，持续下降）
- **video_audio_diff**: 全部 ≤ 0.0s ✅
- **素材重复**: 无 ✅
- **"手持展示"兜底**: 无 ✅

### 是否建议进入下一阶段

**建议进入下一阶段**。理由：
1. 5/5 全部通过，通过率 100%
2. 所有 video_audio_diff 均在 0.0s，完美对齐
3. low_confidence 总数从 34 降至 6，降幅 82%
4. 剩余 6 个 low_confidence 均为"产品展示"兜底，属于安全标签，不影响视频质量
5. 无素材重复、无"手持展示"兜底

### 后续可选优化（非阻塞）

1. 进一步扩充口语化关键词（"别看它小"、"几分钟就搞定"、"但它的本事"等），可将 low_confidence 从 6 降至更低
2. 引入 visual_grouping 优化视觉节奏
3. 引入 end_hold 片尾定格
4. 字幕样式池多样化

---

**报告生成时间**: 2026-07-16  
**验证版本**: batch_fix2_01 ~ batch_fix2_05  
**代码变更**:
- `material_matching_node.py`: 新增口语化关键词映射
- `timeline_assembly_node.py`: 修复跨句视觉延续clip时长不足时的回退逻辑
