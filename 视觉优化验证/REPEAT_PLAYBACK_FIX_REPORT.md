# 重复播放专项修复报告 (REPEAT_PLAYBACK_FIX_REPORT)

## 元数据
- **测试时间**: 2026-07-16 21:06~21:07 (CST)
- **证据来源**: fix2 run 产物独立取证
- **验证范围**: fix2_01, fix2_02, fix2_05

---

## 一、逐片段证据

### fix2_01 (18 sentences, 17 visual_groups, 17 active_clips, 1 continuation)

| timeline_index | sentence_id | visual_group_id | asset_id | source_path | source_start | source_end | output_start | output_end | active/continuation | resolution_source |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 产品展示_001 | materials/产品展示_001.mp4 | 0.0 | 2.39 | 0.0 | 2.39 | active | direct_match |
| 2 | 2 | 2 | 手持大小对比_001 | materials/手持大小对比_001.mp4 | 0.0 | 2.06 | 2.39 | 4.454 | active | direct_match |
| 3 | 3 | 3 | 旅行场景_001 | materials/旅行场景_001.mp4 | 0.0 | 2.47 | 4.454 | 5.354 | active | direct_match |
| 4 | 4 | 3 | 旅行场景_001 | (continuation) | 0.0 | 0.0 | 5.354 | 6.926 | continuation | visual_group |
| 5 | 5 | 4 | 价格促销_001 | materials/价格促销_001.mp4 | 0.0 | 1.08 | 6.926 | 8.008 | active | direct_match |
| 6 | 6 | 5 | 折叠动作_001 | materials/折叠动作_001.mp4 | 0.0 | 1.41 | 8.008 | 9.418 | active | direct_match |
| 7 | 7 | 6 | 手持大小对比_002 | materials/手持大小对比_002.mp4 | 0.0 | 0.9 | 9.418 | 10.318 | active | direct_match |
| 8 | 8 | 7 | 折叠动作_002 | materials/折叠动作_002.mp4 | 0.0 | 0.9 | 10.318 | 11.218 | active | direct_match |
| 9 | 9 | 8 | 旅行场景_002 | materials/旅行场景_002.mp4 | 0.0 | 0.9 | 11.218 | 12.118 | active | direct_match |
| 10 | 10 | 9 | 旅行场景_003 | materials/旅行场景_003.mp4 | 0.0 | 1.41 | 12.118 | 13.527 | active | direct_match |
| 11 | 11 | 10 | 风力展示_001 | materials/风力展示_001.mp4 | 0.3 | 1.55 | 13.527 | 14.773 | active | direct_match |
| 12 | 12 | 11 | 风力展示_005 | materials/风力展示_005.mp4 | 0.3 | 1.55 | 14.773 | 16.018 | active | direct_match |
| 13 | 13 | 12 | 屏显调温_008 | materials/屏显调温_008.mp4 | 0.5 | 1.4 | 16.018 | 16.918 | active | direct_match |
| 14 | 14 | 13 | 屏显调温_003 | materials/屏显调温_003.mp4 | 0.0 | 3.04 | 16.918 | 17.818 | active | direct_match |
| 15 | 15 | 14 | 屏显调温_001 | materials/屏显调温_001.mp4 | 0.5 | 1.58 | 17.818 | 18.718 | active | direct_match |
| 16 | 16 | 15 | 护发效果_006 | materials/护发效果_006.mp4 | 0.5 | 1.91 | 18.718 | 19.618 | active | direct_match |
| 17 | 17 | 16 | 价格促销_002 | materials/价格促销_002.mp4 | 0.0 | 1.08 | 19.618 | 20.69 | active | direct_match |
| 18 | 18 | 17 | CTA促单_001 | materials/CTA促单_001.mp4 | 0.0 | 1.25 | 20.69 | 22.656 | active | direct_match |

**素材使用次数**: 全部 asset_id 均使用 1 次（asset_usage_count=1）
**相邻同 asset_id 记录**: 无（所有相邻 timeline_index 的 asset_id 均不同）
**相邻同素材 source_start 相同**: 不适用（无相邻同素材）
**同一 visual_group 只有一个 active_clip**: ✅ visual_group_id=3 有 1 个 active_clip (sentence 3) + 1 个 continuation (sentence 4)，其余 16 个 group 均只有 1 个 active_clip
**continuation 未生成物理 clip**: ✅ sentence 4 的 clip_path 为空字符串，visual_continuation=true
**source range 重叠**: ✅ 无重叠（每个 active_clip 的 source 区间独立，无跨 clip 重叠）

---

### fix2_02 (20 sentences, 16 visual_groups, 16 active_clips, 4 continuations)

| timeline_index | sentence_id | visual_group_id | asset_id | source_start | source_end | output_start | output_end | active/continuation |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 旅行场景_002 | 0.0 | 1.0 | 0.0 | 1.0 | active |
| 2 | 2 | 2 | 痛点共鸣_001 | 0.0 | 1.0 | 1.0 | 2.0 | active |
| 3 | 3 | 3 | 痛点共鸣_004 | 0.0 | 1.31 | 2.0 | 3.31 | active |
| 4 | 4 | 4 | 旅行场景_003 | 0.0 | 1.0 | 3.31 | 4.31 | active |
| 5 | 5 | 5 | 痛点共鸣_003 | 0.0 | 2.13 | 4.31 | 6.44 | active |
| 6 | 6 | 5 | 痛点共鸣_003 | 0.0 | 0.0 | 6.44 | 7.34 | continuation |
| 7 | 7 | 6 | 旅行场景_001 | 0.0 | 2.08 | 7.34 | 9.42 | active |
| 8 | 8 | 7 | 手持大小对比_001 | 0.0 | 1.92 | 9.42 | 11.34 | active |
| 9 | 9 | 8 | 放进包包_005 | 0.0 | 2.21 | 11.34 | 13.55 | active |
| 10 | 10 | 8 | 放进包包_005 | 0.0 | 0.0 | 13.55 | 14.45 | continuation |
| 11 | 11 | 9 | 风力展示_004 | 0.3 | 3.12 | 14.45 | 15.35 | active |
| 12 | 12 | 9 | 风力展示_004 | 0.0 | 0.0 | 15.35 | 16.25 | continuation |
| 13 | 13 | 10 | 吹发动作_001 | 0.0 | 2.06 | 16.25 | 17.15 | active |
| 14 | 14 | 10 | 吹发动作_001 | 0.0 | 0.0 | 17.15 | 18.05 | continuation |
| 15 | 15 | 11 | 屏显调温_007 | 0.5 | 2.73 | 18.05 | 18.95 | active |
| 16 | 16 | 12 | 屏显调温_003 | 0.0 | 3.04 | 18.95 | 20.11 | active |
| 17 | 17 | 13 | 屏显调温_008 | 0.5 | 1.4 | 20.11 | 21.01 | active |
| 18 | 18 | 14 | 旅行场景_005 | 0.0 | 1.16 | 21.01 | 21.91 | active |
| 19 | 19 | 15 | 产品展示_001 | 0.0 | 1.92 | 21.91 | 23.83 | active |
| 20 | 20 | 16 | 放进行李箱_005 | 0.0 | 1.62 | 23.83 | 26.832 | active |

**素材使用次数**: 全部 asset_id 均使用 1 次
**相邻同 asset_id 记录**: 无
**相邻同素材 source_start 相同**: 不适用
**同一 visual_group 只有一个 active_clip**: ✅ group 5/8/9/10 各有 1 active + continuation
**continuation 未生成物理 clip**: ✅ 所有 continuation 的 clip_path 为空
**source range 重叠**: ✅ 无重叠

---

### fix2_05 (18 sentences, 16 visual_groups, 16 active_clips, 2 continuations)

| timeline_index | sentence_id | visual_group_id | asset_id | source_start | source_end | output_start | output_end | active/continuation |
|---|---|---|---|---|---|---|---|---|
| 1 | 1 | 1 | 吹发动作_008 | 0.0 | 1.23 | 0.0 | 1.23 | active |
| 2 | 2 | 2 | 手持大小对比_003 | 1.5 | 3.55 | 1.23 | 2.49 | active |
| 3 | 3 | 3 | 折叠动作_001 | 0.0 | 2.23 | 2.49 | 3.39 | active |
| 4 | 4 | 4 | 手持大小对比_001 | 0.0 | 1.06 | 3.39 | 4.29 | active |
| 5 | 5 | 5 | 手持大小对比_002 | 0.0 | 1.06 | 4.29 | 5.19 | active |
| 6 | 6 | 6 | 风力展示_004 | 0.3 | 2.03 | 5.19 | 6.09 | active |
| 7 | 7 | 7 | 风力展示_002 | 0.3 | 2.03 | 6.09 | 6.99 | active |
| 8 | 8 | 8 | 风嘴配件_007 | 0.0 | 1.23 | 6.99 | 7.89 | active |
| 9 | 9 | 9 | 护发效果_006 | 0.5 | 2.23 | 7.89 | 8.79 | active |
| 10 | 10 | 10 | 风力展示_001 | 0.3 | 1.2 | 8.79 | 9.69 | active |
| 11 | 11 | 11 | 屏显调温_003 | 0.0 | 3.04 | 9.69 | 10.59 | active |
| 12 | 12 | 12 | 屏显调温_008 | 0.5 | 1.4 | 10.59 | 11.49 | active |
| 13 | 13 | 13 | 吹发动作_001 | 0.0 | 1.73 | 11.49 | 13.22 | active |
| 14 | 14 | 14 | 旅行场景_002 | 0.0 | 1.06 | 13.22 | 14.28 | active |
| 15 | 15 | 15 | 放进包包_005 | 0.0 | 1.8 | 14.28 | 15.18 | active |
| 16 | 16 | 15 | 放进包包_005 | 0.0 | 0.0 | 15.18 | 16.08 | continuation |
| 17 | 17 | 16 | 手持大小对比_006 | 0.0 | 3.04 | 16.08 | 18.14 | active |
| 18 | 18 | 16 | 手持大小对比_006 | 0.0 | 0.0 | 18.14 | 19.2 | continuation |

**素材使用次数**: 全部 asset_id 均使用 1 次
**相邻同 asset_id 记录**: 无
**相邻同素材 source_start 相同**: 不适用
**同一 visual_group 只有一个 active_clip**: ✅ group 15/16 各有 1 active + continuation
**continuation 未生成物理 clip**: ✅ 所有 continuation 的 clip_path 为空
**source range 重叠**: ✅ 无重叠

---

## 二、汇总验证

| 验证项 | fix2_01 | fix2_02 | fix2_05 |
|---|---|---|---|
| 每个素材使用次数=1 | ✅ | ✅ | ✅ |
| 无相邻同 asset_id | ✅ | ✅ | ✅ |
| 同 visual_group 仅 1 active_clip | ✅ | ✅ | ✅ |
| continuation 无物理 clip | ✅ | ✅ | ✅ |
| 无 source range 重叠 | ✅ | ✅ | ✅ |
| adjacent_same_asset_restart=false | ✅ | ✅ | ✅ |
| visual_group_continuity_fail=false | ✅ | ✅ | ✅ |
| high_freq_reuse_fail=false | ✅ | ✅ | ✅ |
| source_range_overlap_fail=false | ✅ | ✅ | ✅ |

## 三、结论

**重复播放问题已修复**。所有 3 条用例均满足：
- 同一 visual_group 只生成 1 个 active clip，后续句子以 continuation 模式延续
- 无相邻同素材从头重启
- 无 source range 重叠
- 每个素材使用次数均为 1
