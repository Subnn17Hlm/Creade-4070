# 回退指南 — ROLLBACK_NOTES

**备份名称**: LOCK_RELEASE_主链路稳定版_2026-07-16  
**备份日期**: 2026-07-16

---

## 何时需要回退

当后续优化（visual_grouping、end_hold、字幕样式池、字体随机、LLM兜底等）导致以下异常时，应回退到本版本：
- 工作流运行报错（节点异常、图编译失败）
- 视频生成失败（final.mp4无法输出）
- 质量验收不通过（5条小批量通过率 < 80%）
- video_audio_diff > 0.5s
- 素材匹配异常（low_confidence总数 > 10）
- 字幕渲染异常（字幕不可见、样式错误）
- 音视频不同步

## 回退步骤

### 步骤1：恢复核心代码

从 `code_snapshot/` 目录复制以下文件回原位：

```bash
# 核心节点代码
cp code_snapshot/src/graphs/nodes/material_matching_node.py src/graphs/nodes/
cp code_snapshot/src/graphs/nodes/clip_extraction_node.py src/graphs/nodes/
cp code_snapshot/src/graphs/nodes/timeline_assembly_node.py src/graphs/nodes/
cp code_snapshot/src/graphs/nodes/final_composition_node.py src/graphs/nodes/
cp code_snapshot/src/graphs/nodes/script_source_router_node.py src/graphs/nodes/
cp code_snapshot/src/graphs/nodes/quality_check_node.py src/graphs/nodes/

# 图编排与状态定义
cp code_snapshot/src/graphs/graph.py src/graphs/
cp code_snapshot/src/graphs/state.py src/graphs/
```

### 步骤2：恢复素材清单

```bash
cp asset_manifest_snapshot/asset_manifest_v2_clean.csv assets/
```

### 步骤3：恢复配置文件（如有变更）

```bash
cp config_snapshot/*.json config/
```

### 步骤4：对照检查

- 对照 `LOCK_FILE_LIST.md` 检查所有关键文件是否已恢复
- 确认 `code_snapshot/` 中的文件与 `src/graphs/` 中的文件一致

### 步骤5：重新验证（必须）

恢复代码后，**必须重新跑5条小批量验证**，不要只看代码：

```
使用以下5条文案重新验证：
1. batch_fix2_01 文案（见 runs/batch_fix2_01/original_script.txt）
2. batch_fix2_02 文案（见 runs/batch_fix2_02/original_script.txt）
3. batch_fix2_03 文案（见 runs/batch_fix2_03/original_script.txt）
4. batch_fix2_04 文案（见 runs/batch_fix2_04/original_script.txt）
5. batch_fix2_05 文案（见 runs/batch_fix2_05/original_script.txt）
```

验收标准：
- 每条 low_confidence < 3
- 每条 video_audio_diff ≤ 0.5s
- 不使用"手持展示"兜底
- 无素材高频重复
- 5条全部 status=success

### 步骤6：对照验证报告

对照 `validation_report/BATCH_VALIDATION_REPORT.md` 确认恢复后的行为与备份时一致。

## 注意事项

- 回退后应检查 `runs/` 目录下的旧验证产物是否仍然存在，可用于对比
- 如果回退后仍然异常，可能是资源文件（字体、BGM、素材URL）发生变化，需检查 `assets/` 目录
- 本备份不包含视频素材文件本身，只包含素材清单CSV。如果素材URL过期，需要重新生成presigned_url
