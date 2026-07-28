# 5条小批量验证报告（修复版）

**生成时间**: 2026-07-16  
**验证目的**: 验证规则版语义匹配优化后的稳定性  
**优化内容**:
1. 扩充 `_KEYWORD_TO_TAG` 字典（从 ~50 扩展到 ~150 关键词）
2. 优化兜底策略：按句子类型选择更安全标签（CTA促单/价格促销/痛点共鸣/旅行场景/放进包包/产品展示）
3. 移除"手持展示"兜底标签
4. 修复兜底素材重复问题（使用 `used_material_ids` 跟踪）

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
| batch_fix_01 | ✅ PASSED | 2 | -0.112s | 无 | 否 |
| batch_fix_02 | ❌ FAILED | 4 | -0.272s | 无 | 否 |
| batch_fix_03 | ✅ PASSED | 0 | 0.000s | 无 | 否 |
| batch_fix_04 | ❌ FAILED | 3 | -1.064s | 无 | 否 |
| batch_fix_05 | ✅ PASSED | 0 | -0.296s | 无 | 否 |

**通过率**: 3/5 (60%)

---

## 详细分析

### batch_fix_01 ✅ PASSED

**文案**: Creade终于把高性能的风...  
**分句数**: 18  
**唯一素材数**: 18  
**low_confidence**: 2 (< 3 ✅)  
**video_audio_diff**: -0.112s (≤ 0.5s ✅)  
**重复素材**: 无 ✅  
**手持展示兜底**: 否 ✅  
**BGM**: bgm_07.mp3  

**low_confidence 句子**:
- 句3: "就是这款" → 产品展示 (fallback: 无明确关键词)
- 句4: "随行折叠高速吹风机" → 手持大小对比 (fallback: 无明确关键词)

**final.mp4**: `runs/batch_fix_01/final.mp4`  
**final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_0e397f7e.mp4?sign=1784230488-78109a7ffb-0-63a4ad874d4b4c964a902679279dc13cc9f7dd9da4670706a3f9bc0da607a8b9

---

### batch_fix_02 ❌ FAILED

**文案**: 每次出差旅行...  
**分句数**: 20  
**唯一素材数**: 20  
**low_confidence**: 4 (≥ 3 ❌)  
**video_audio_diff**: -0.272s (≤ 0.5s ✅)  
**重复素材**: 无 ✅  
**手持展示兜底**: 否 ✅  
**BGM**: bgm_03.mp3  

**low_confidence 句子**:
- 句2: "是不是都在为" → 痛点共鸣 (fallback: 无明确关键词)
- 句6: "还好被我挖到了这个出差神器" → 旅行场景 (fallback: 无明确关键词)
- 句15: "这个颜值高又强悍的小东西" → 手持大小对比 (fallback: 无明确关键词)
- 句16: "必须焊在你的行李箱里！" → 放进行李箱 (fallback: 无明确关键词)

**失败原因**: low_confidence_segments=4 ≥ 3

**final.mp4**: `runs/batch_fix_02/final.mp4`  
**final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_313eca17.mp4?sign=1784230635-cad1e6da79-0-f4cca7461c03c183f9a63fabe48744bcebe10abd8854a27a39d1c8fd6e00c32f

---

### batch_fix_03 ✅ PASSED

**文案**: 不挑包包 不占空间...  
**分句数**: 15  
**唯一素材数**: 15  
**low_confidence**: 0 (< 3 ✅)  
**video_audio_diff**: 0.000s (≤ 0.5s ✅)  
**重复素材**: 无 ✅  
**手持展示兜底**: 否 ✅  
**BGM**: bgm_05.mp3  

**low_confidence 句子**: 无

**final.mp4**: `runs/batch_fix_03/final.mp4`  
**final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_6439b15c.mp4?sign=1784230773-a6ef21325a-0-f7b4292a42bad4c5abfdadd82dfe6fac58e3c93edd4c5ceb88fb2527af4a363b

---

### batch_fix_04 ❌ FAILED

**文案**: 直降399 到手还是这么多...  
**分句数**: 21  
**唯一素材数**: 21  
**low_confidence**: 3 (≥ 3 ❌)  
**video_audio_diff**: -1.064s (> 0.5s ❌)  
**重复素材**: 无 ✅  
**手持展示兜底**: 否 ✅  
**BGM**: bgm_09.mp3  

**low_confidence 句子**:
- 句2: "到手还是这么多" → 价格促销 (fallback: 无明确关键词)
- 句8: "——再不买 真没了" → CTA促单 (fallback: 无明确关键词)
- 句15: "也就一个科瑞德的事" → 产品展示 (fallback: 无明确关键词)

**失败原因**: 
1. low_confidence_segments=3 ≥ 3
2. video_audio_diff=-1.064s > 0.5s

**final.mp4**: `runs/batch_fix_04/final.mp4`  
**final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_37f8a41d.mp4?sign=1784230925-78a37b603d-0-8e529ca9a2ce2ab62e76a7f465449c16edd58474f343aad4d772b37479b1f230

---

### batch_fix_05 ✅ PASSED

**文案**: 这么小的吹风机...  
**分句数**: 18  
**唯一素材数**: 18  
**low_confidence**: 0 (< 3 ✅)  
**video_audio_diff**: -0.296s (≤ 0.5s ✅)  
**重复素材**: 无 ✅  
**手持展示兜底**: 否 ✅  
**BGM**: bgm_11.mp3  

**low_confidence 句子**: 无

**final.mp4**: `runs/batch_fix_05/final.mp4`  
**final_video_url**: https://coze-coding-project.tos.coze.site/coze_storage_7662258808986730531/final_2bdc7396.mp4?sign=1784231072-9c2f4271bc-0-dfc50d7eae40c11924f7125ac8e5524b41362bce825158bfc41928bd1e19d46e

---

## 优化效果对比

### 关键词覆盖提升

| 批次 | 修复前 low_confidence | 修复后 low_confidence | 改善 |
|------|----------------------|----------------------|------|
| batch_01 / batch_fix_01 | 6 | 2 | -67% |
| batch_02 / batch_fix_02 | 4 | 4 | 0% |
| batch_03 / batch_fix_03 | 3 | 0 | -100% |
| batch_04 / batch_fix_04 | 14 | 3 | -79% |
| batch_05 / batch_fix_05 | 7 | 0 | -100% |
| **总计** | **34** | **9** | **-74%** |

### 素材重复修复

| 批次 | 修复前重复素材 | 修复后重复素材 |
|------|---------------|---------------|
| batch_04 | 产品展示_001 重复7次 | 无重复 |

### 兜底策略优化

- **修复前**: 无匹配时默认使用"手持展示"兜底
- **修复后**: 按句子类型选择更安全标签（CTA促单/价格促销/痛点共鸣/旅行场景/放进包包/产品展示）

---

## 仍存在的问题

### 1. batch_fix_02 和 batch_fix_04 仍有较多 low_confidence

**batch_fix_02 (low_confidence=4)**:
- 口语化表达较多："是不是都在为"、"焊在你的行李箱里"等
- 这些表达没有明确的关键词可以匹配

**batch_fix_04 (low_confidence=3)**:
- 促销类表达："到手还是这么多"、"再不买 真没了"
- 虽然扩充了促销关键词，但部分表达仍然无法精确匹配

### 2. video_audio_diff 超标

**batch_fix_04**: video_audio_diff=-1.064s > 0.5s

可能原因：
- full_play_required 素材跨句延续逻辑导致视觉总时长与TTS时长不对齐
- 需要检查 timeline_assembly_node.py 中的跨句延续逻辑

---

## 后续优化建议

### 立即优化（优先级高）

1. **扩充口语化表达关键词**:
   - "是不是" → 痛点共鸣
   - "焊在" → 旅行场景/放进包包
   - "挖到" → 旅行场景
   - "神器" → 旅行场景

2. **修复 video_audio_diff 超标问题**:
   - 检查 batch_fix_04 的 full_play_required 素材跨句延续逻辑
   - 确保视觉总时长与 TTS 时长对齐

### 短期优化（优先级中）

1. **增加更多口语化种草表达**:
   - "真的会谢" → 痛点共鸣
   - "绝了" → 产品展示
   - "爱了" → 产品展示

2. **优化兜底素材选择**:
   - 当兜底到同一标签时，确保轮换不同素材

### 长期优化（优先级低）

1. **引入大模型兜底**: 当规则匹配失败时，使用 LLM 进行语义理解
2. **字幕样式池**: 支持多样式随机选择
3. **字体随机选择**: 支持多字体随机选择

---

## 结论

### 当前状态

- **通过率**: 3/5 (60%)
- **主要问题**: low_confidence 仍偏高（2条视频 ≥ 3）
- **已修复**: 素材重复、"手持展示"兜底

### 是否建议进入下一阶段

**暂不建议**。原因：
1. 通过率 60% 未达到 80% 目标
2. batch_fix_04 存在 video_audio_diff 超标问题

### 下一步行动

1. 完成"立即优化"项后重新验证
2. 重点修复口语化表达关键词覆盖
3. 修复 video_audio_diff 超标问题

---

**报告生成时间**: 2026-07-16  
**验证版本**: batch_fix_01 ~ batch_fix_05  
**代码版本**: material_matching_node.py (优化后)
