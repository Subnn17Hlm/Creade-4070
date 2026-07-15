# LOCK_RELEASE_REPORT

## 备份信息

- **备份名称**: LOCK_RELEASE_主链路稳定版_2026-07-16
- **备份日期**: 2026-07-16
- **备份类型**: main_pipeline_stable（主链路稳定版）
- **备份目的**: 作为后续 visual_grouping、end_hold、字幕样式池等观感优化前的安全回滚点

## 验证结果

| 指标 | 值 |
|------|-----|
| 当前通过的小批量版本 | batch_fix2_01 ~ batch_fix2_05 |
| 通过率 | 5/5 (100%) |
| low_confidence 总数 | 6 |
| 是否使用"手持展示"兜底 | 否 |
| 是否有素材高频重复 | 否 |
| video_audio_diff | 5条全部 <= 0.5s（实际全部为 0.000s） |
| 素材清单 | assets/asset_manifest_v2_clean.csv（126个素材） |
| BGM目录 | assets/bgm/ |

## 各批次详情

| 批次 | low_confidence | video_audio_diff | 状态 |
|------|----------------|------------------|------|
| batch_fix2_01 | 2 | 0.000s | ✅ success |
| batch_fix2_02 | 2 | 0.000s | ✅ success |
| batch_fix2_03 | 0 | 0.000s | ✅ success |
| batch_fix2_04 | 2 | 0.000s | ✅ success |
| batch_fix2_05 | 0 | 0.000s | ✅ success |

## 是否建议作为后续优化前回滚点

**是**。理由：
1. 5/5 全部通过，通过率 100%
2. 所有 video_audio_diff 均为 0.000s，完美对齐
3. low_confidence 总数从初始 34 降至 6，降幅 82%
4. 剩余 6 个 low_confidence 均为"产品展示"安全兜底，不影响视频质量
5. 无素材重复、无"手持展示"兜底

## 后续允许优化项

以下优化项可在本备份之后进行，但不得直接覆盖本备份：
- visual_grouping（视觉分组优化）
- end_hold（片尾定格）
- 字幕样式池（多样式随机选择）
- 字体随机选择
- LLM兜底（规则匹配失败时使用大模型语义理解）

## 约束

- 后续优化不得直接覆盖此备份目录
- 后续优化如导致工作流异常，应回退到本 LOCK_RELEASE
- 本备份目录中的 code_snapshot/ 可用于恢复核心代码

## 来源验证报告

`/workspace/projects/5条小批量验证修复版2/BATCH_VALIDATION_REPORT.md`
