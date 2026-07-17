# 素材质量优化 Smoke Test 报告

## 测试信息
- **测试用例**: material_quality_smoke_04
- **输入文案**: runs/batch_fix2_04/original_script.txt
- **测试时间**: 2026-07-16
- **Run ID**: 667b245e-9ded-4d84-ac41-0c7f36e1e9e9

## 核心指标

| 指标 | 要求 | 实际值 | 状态 |
|------|------|--------|------|
| status | success | success | **PASS** |
| body_sync_diff | ≤ 0.2s | 0.032s | **PASS** |
| low_confidence_segments | < 3 | 1 | **PASS** |
| adjacent_same_asset_restart | false | false | **PASS** |
| visual_group_continuity_fail | false | false | **PASS** |
| high_freq_reuse_fail | false | false | **PASS** |
| source_range_overlap_fail | false | false | **PASS** |
| max_body_freeze_duration | < 1.2s | 0.96s | **PASS** (warning) |
| failure_category | fully_successful | fully_successful | **PASS** |

## 白名单素材验证

### 屏显调温_003 使用情况
- **visual_group_id**: 14
- **sentence_id**: 18
- **output_start**: 19.122s
- **output_end**: 20.022s

### 字幕关闭验证
- **subtitle_suppression_intervals.json**: 已生成 ✅
- **render_subtitles.srt**: 已生成 ✅
- **关闭区间**: 19.122s - 20.022s
- **关闭前字幕**: Cue 17 "枯草变瀑布" 结束于 19.122s ✅
- **关闭后字幕**: Cue 18 "十种温档调节" 开始于 20.022s ✅
- **关闭期间无系统字幕**: ✅

### 验证结论
1. 白名单素材被正确选中 ✅
2. 字幕关闭区间正确 ✅
3. 关闭期间无系统字幕 ✅
4. 关闭结束后字幕恢复 ✅
5. canonical subtitles.srt 完整 ✅
6. render_subtitles.srt 正确 ✅

## 素材使用统计

| 素材ID | 使用次数 |
|--------|----------|
| 价格促销_003 | 1 |
| 手持大小对比_003 | 1 |
| 手持大小对比_001 | 1 |
| 旅行场景_002 | 1 |
| 放进行李箱_005 | 1 |
| 产品展示_001 | 1 |
| 价格促销_001 | 1 |
| 价格促销_002 | 1 |
| 痛点共鸣_003 | 1 |
| 护发效果_003 | 1 |
| 风力展示_001 | 1 |
| 风力展示_005 | 1 |
| 护发效果_004 | 1 |
| **屏显调温_003** | **1** |
| 屏显调温_008 | 1 |
| 护发效果_005 | 1 |
| 产品展示_002 | 1 |

- **唯一素材数**: 17
- **无高频复用**: ✅
- **无相邻重播**: ✅

## 主体静帧检测

| 静帧段 | 开始 | 结束 | 时长 | 是否End Hold |
|--------|------|------|------|-------------|
| 1 | 0.08s | 0.64s | 0.56s | 否 |
| 2 | 1.60s | 2.16s | 0.56s | 否 |
| 3 | 8.48s | 9.04s | 0.56s | 否 |
| 4 | 17.76s | 18.40s | 0.64s | 否 |
| 5 | 21.28s | 22.00s | 0.72s | 否 |
| 6 | 22.48s | 23.44s | 0.96s | 否 |

- **最大主体静帧**: 0.96s (< 1.2s ✅)
- **静帧状态**: warning (≥ 0.8s 但 < 1.2s)
- **End Hold 静帧**: 不计入主体失败 ✅

## 其他检查

| 检查项 | 状态 |
|--------|------|
| TTS 存在 | ✅ |
| BGM 存在 | ✅ |
| 音频混合成功 | ✅ |
| 字幕烧录成功 | ✅ |
| 字幕可见 | ✅ |
| 无黑屏 | ✅ |
| 无暗场 | ✅ |
| 无烧录文字 | ✅ |

## 最终结论

**Smoke Test 通过** ✅

所有核心指标均满足要求：
1. 白名单素材被正确选中并使用
2. 字幕关闭功能正常工作
3. body_sync_diff ≤ 0.2s
4. low_confidence_segments < 3
5. 无重复播放和高频滥用
6. 主体静帧 < 1.2s
7. final.mp4 可完整解码
8. 无黑屏、空白、音频缺失
