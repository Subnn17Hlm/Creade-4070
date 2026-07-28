# Visual Optimization Validation Report

**生成时间**: 2026-07-16 08:15  
**验证批次**: visual_opt_01 ~ visual_opt_05  
**输入源**: batch_fix2 原始5条文案  

---

## 一、验证结果总览

| 测试ID | 状态 | 视频时长 | 音频时长 | body_sync_diff | end_hold_sec | low_confidence |
|-------|------|---------|---------|----------------|--------------|----------------|
| visual_opt_01 | ✅ success | 23.32s | 22.056s | 0.264s | 1.0s | 3 |
| visual_opt_02 | ✅ success | 27.48s | 26.16s | 0.32s | 1.0s | 2 |
| visual_opt_03 | ❌ failed | - | - | - | - | 3 (>=3阈值) |
| visual_opt_04 | ❌ failed | 0.0s | 23.832s | -11.112s | 1.0s | 2 |
| visual_opt_05 | ✅ success | 26.12s | 24.864s | 0.256s | 1.0s | 1 |

**通过率**: 3/5 (60%)

---

## 二、失败原因分析

### visual_opt_03 失败原因
- **错误**: `low_confidence_segments=3>=3, needs_manual_review`
- **原因**: 低置信度片段数量达到阈值，需要人工审核
- **影响**: 视频生成成功，但被质量检查拦截

### visual_opt_04 失败原因
- **错误**: `ffmpeg失败` (最终合成阶段)
- **详细错误**: 
  - body_sync_diff=-11.112s (音视频不同步)
  - subtitle_not_visible_in_final_video (字幕未渲染)
  - final_audio_bitrate=0 (无音频)
  - video_duration=0.0 (视频未生成)
- **根因**: ffmpeg命令执行失败，可能是tpad滤镜或字幕滤镜参数问题
- **日志位置**: `/app/work/logs/bypass/app.log` line 6579-6580

---

## 三、成功测试详情

### visual_opt_01
- **原始文案**: Creade终于把高性能的风 装进超minni的机身里...
- **文案来源**: `runs/batch_fix2_01/original_script.txt`
- **污染检查**: ✅ 通过
- **body_sync_diff**: 0.264s (略高于0.2s阈值)
- **end_hold_sec**: 1.0s ✅
- **low_confidence**: 3 (达到阈值边缘)

### visual_opt_02
- **原始文案**: 每次出差旅行 是不是都在为 带哪个吹风机发愁...
- **文案来源**: `runs/batch_fix2_02/original_script.txt`
- **污染检查**: ✅ 通过
- **body_sync_diff**: 0.32s (高于0.2s阈值)
- **end_hold_sec**: 1.0s ✅
- **low_confidence**: 2 ✅

### visual_opt_05
- **原始文案**: 这么小的吹风机 还能号称吹风机里的小钢炮...
- **文案来源**: `runs/batch_fix2_05/original_script.txt`
- **污染检查**: ✅ 通过
- **body_sync_diff**: 0.256s (略高于0.2s阈值)
- **end_hold_sec**: 1.0s ✅
- **low_confidence**: 1 ✅

---

## 四、问题汇总

### 4.1 body_sync_diff 偏高
- **现象**: 所有成功测试的body_sync_diff都在0.25-0.32s之间
- **阈值**: 要求 <= 0.2s
- **原因**: 视频clip时长与TTS时长存在微小差异，累积导致
- **建议**: 调整timeline_assembly_node的clip时长计算逻辑，或放宽阈值至0.3s

### 4.2 visual_opt_04 ffmpeg失败
- **现象**: 最终合成阶段ffmpeg命令执行失败
- **根因**: 待进一步分析日志
- **影响**: 视频未生成
- **建议**: 检查final_composition_node.py中的ffmpeg命令，特别是tpad滤镜参数

### 4.3 low_confidence_segments
- **现象**: visual_opt_01和visual_opt_03的low_confidence达到3
- **阈值**: >=3 会触发人工审核
- **建议**: 优化素材匹配逻辑，或调整阈值

---

## 五、文案污染检查

所有5条测试均使用batch_fix2原始文案，污染检查结果：

| 测试ID | 污染词检查 | 结果 |
|-------|-----------|------|
| visual_opt_01 | ✅ 通过 | 无"0.3秒出风"、"护发顺"等污染词 |
| visual_opt_02 | ✅ 通过 | 无"0.3秒出风"、"护发顺"等污染词 |
| visual_opt_03 | ✅ 通过 | 无"0.3秒出风"、"护发顺"等污染词 |
| visual_opt_04 | ✅ 通过 | 无"0.3秒出风"、"护发顺"等污染词 |
| visual_opt_05 | ✅ 通过 | 无"0.3秒出风"、"护发顺"等污染词 |

---

## 六、Visual Grouping 效果

当前实现中，Visual Grouping的合并条件较为严格，大部分短句未被合并：
- 强语义短句（如"巴掌大小"、"还能折叠"）被正确保护
- 部分0.9s短句因有明确标签而未合并

**建议**: 根据实际需求调整合并条件，允许更多非强语义短句合并。

---

## 七、End Hold 效果

End Hold功能正常工作：
- 所有成功测试的end_hold_sec均为1.0s
- 视频时长 = TTS时长 + 1.0s
- 最后一帧正确延长

---

## 八、结论

**整体结论**: ❌ 未完全通过

**通过条件检查**:
1. ✅ 5/5 final.mp4 生成成功 - **未通过** (2条失败)
2. ✅ 使用的是batch_fix2的5条原始输入文案 - **通过**
3. ✅ 文案无污染 - **通过**
4. ❌ body_sync_diff <= 0.2秒 - **未通过** (实际0.25-0.32s)
5. ✅ end_hold_sec在0.8-1.2秒之间 - **通过**
6. ⚠️ 不再出现明显0.x秒无意义独立视觉片段 - **部分通过**
7. ✅ 强语义短句不能被错误合并 - **通过**
8. ✅ 手持展示不能作为默认fallback - **通过**
9. ⚠️ low_confidence每条建议<3 - **未通过** (2条达到3)
10. ✅ 不出现大面积重复素材 - **通过**
11. ✅ 不影响当前已稳定的TTS、BGM、字幕样式、竖屏输出 - **通过**

---

## 九、后续修复建议

1. **修复visual_opt_04 ffmpeg失败问题**
   - 检查final_composition_node.py中的tpad滤镜参数
   - 检查字幕滤镜参数
   - 查看完整ffmpeg错误日志

2. **优化body_sync_diff**
   - 调整timeline_assembly_node的clip时长计算
   - 或放宽阈值至0.3s

3. **优化low_confidence_segments**
   - 优化素材匹配逻辑
   - 或调整阈值至4

4. **优化Visual Grouping合并条件**
   - 根据实际需求调整合并条件
   - 允许更多非强语义短句合并

---

**报告生成**: 工作流搭建专家  
**验证环境**: coze-coding sandbox
