# 失败兜底专项 Smoke 测试报告

**测试时间**: 2025-01-24
**测试范围**: 阶段2 - 失败兜底专项验证 (03, 04)
**验证目标**: 确认 low_confidence 可控、FFmpeg 不再失败

---

## 测试用例与结果

| 用例 | 文案 | body_sync_diff | end_hold_sec | low_confidence_segments | FFmpeg 状态 | 状态 |
|------|------|---------------|-------------|------------------------|------------|------|
| visual_opt_lowconf_smoke_03 | 不挑包包 不占空间... | 0.024s | 1.0s | **0** | 成功 | PASS |
| visual_opt_ffmpeg_smoke_04 | 直降399 到手还是这么多... | 0.032s | 1.0s | **1** | 成功 | PASS |

---

## 关键指标验证

### 1. low_confidence_segments
- **要求**: < 3
- **结果**: 0 / 1
- **结论**: PASS

### 2. FFmpeg 合成
- **要求**: 不失败
- **结果**: 两条均成功生成最终视频
- **结论**: PASS

### 3. body_sync_diff
- **要求**: <= 0.2s
- **结果**: 0.024s / 0.032s
- **结论**: PASS (远低于阈值)

---

## 修复措施回顾

1. **material_matching_node.py**: 匹配失败不再强制使用 `selected_assets[0]`，而是标记 `unmatched=true` 并记录 `unmatched_reason`
2. **final_composition_node.py**: 修复 body_sync_diff —— 在 end_hold 前先将视频 trim 到 TTS 时长，避免视频比音频长
3. **quality_check_node.py**: 新增 `high_freq_reuse_fail` 和 `source_range_overlap_fail` 检查

---

## 结论

**阶段2 PASS** - FFmpeg 失败和 low_confidence 过高问题均已修复，2条测试用例全部通过。
