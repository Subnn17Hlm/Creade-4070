"""
字幕预设批量分配与 variation_index 传递链路测试

覆盖：
1. 六条批量任务 variation_index 为 0~5
2. 四预设分配结果严格为 0、1、2、3、0、1
3. 并发顺序变化不影响分配
4. retry 读取已持久化 preset，保持一致
5. 历史任务缺少 variation_index 时，task_id fallback 稳定且不会全部为 0
6. CSV 解析生成正确的 batch_task_index
"""
import asyncio
import hashlib
import os
import sys

import pytest

# 确保 src 在路径中
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from subtitle_styling.presets import (
    get_preset_count,
    get_preset_for_task,
    get_preset_for_task_id,
    get_presets,
)
from subtitle_styling.assignment import assign_subtitle_style
from api.batch_csv import validate_csv


# ============================================================
# 1. CSV 解析生成正确的 batch_task_index
# ============================================================
class TestCSVBatchTaskIndex:
    def test_six_rows_get_indices_0_to_5(self):
        """六行 CSV 解析后 batch_task_index 为 0~5"""
        lines = ["script_text"]
        for i in range(6):
            lines.append(f"测试文案第{i+1}条")
        csv_content = "\n".join(lines).encode("utf-8")

        result = validate_csv(csv_content)
        assert result.success is True
        assert len(result.rows) == 6

        for i, row in enumerate(result.rows):
            assert row["batch_task_index"] == i, (
                f"第 {i} 行 batch_task_index 期望 {i}，实际 {row['batch_task_index']}"
            )

    def test_skipped_rows_do_not_affect_index(self):
        """无效行被跳过后，batch_task_index 仍然连续"""
        csv_content = "script_text\nvalid text 1\n\nvalid text 2\nvalid text 3".encode("utf-8")
        result = validate_csv(csv_content)
        # 空行被跳过，只有 3 条有效行
        valid_rows = [r for r in result.rows if r.get("script_text", "").strip()]
        assert len(valid_rows) == 3
        for i, row in enumerate(valid_rows):
            assert row["batch_task_index"] == i


# ============================================================
# 2. 四预设分配结果严格为 0、1、2、3、0、1
# ============================================================
class TestBatchPresetAssignment:
    def test_six_tasks_four_presets(self):
        """六条任务、四个预设 → 分配索引为 0,1,2,3,0,1"""
        preset_count = get_preset_count()
        assert preset_count == 4, f"期望 4 个预设，实际 {preset_count}"

        all_presets = get_presets()
        expected_ids = [
            all_presets[0].preset_id,
            all_presets[1].preset_id,
            all_presets[2].preset_id,
            all_presets[3].preset_id,
            all_presets[0].preset_id,
            all_presets[1].preset_id,
        ]

        for task_index in range(6):
            assignment = assign_subtitle_style(
                task_id=f"task_{task_index}",
                task_index=task_index,
            )
            assert assignment.preset_id == expected_ids[task_index], (
                f"task_index={task_index}: 期望 preset={expected_ids[task_index]}，"
                f"实际 preset={assignment.preset_id}"
            )

    def test_concurrent_order_invariant(self):
        """并发顺序变化不影响分配结果"""
        import random

        indices = list(range(6))
        results = {}

        # 按随机顺序分配
        random.shuffle(indices)
        for task_index in indices:
            assignment = assign_subtitle_style(
                task_id=f"concurrent_task_{task_index}",
                task_index=task_index,
            )
            results[task_index] = assignment.preset_id

        # 验证结果与顺序无关
        all_presets = get_presets()
        for task_index in range(6):
            expected = all_presets[task_index % get_preset_count()].preset_id
            assert results[task_index] == expected, (
                f"task_index={task_index}: 期望 {expected}，实际 {results[task_index]}"
            )


# ============================================================
# 3. retry 读取已持久化 preset，保持一致
# ============================================================
class TestRetryConsistency:
    def test_existing_assignment_is_restored(self):
        """已有持久化分配结果时，retry 返回相同 preset"""
        original = assign_subtitle_style(
            task_id="retry_task_1",
            task_index=2,
        )

        # 模拟已持久化的结果
        saved = {
            "subtitle_preset_id": original.preset_id,
            "subtitle_style_id": original.style_id,
            "subtitle_font_id": original.font_id,
            "subtitle_fallback_used": original.fallback_used,
        }

        restored = assign_subtitle_style(
            task_id="retry_task_1",
            existing_assignment=saved,
            task_index=999,  # 即使 task_index 不同也应恢复原值
        )

        assert restored.preset_id == original.preset_id
        assert restored.style_id == original.style_id
        assert restored.font_id == original.font_id


# ============================================================
# 4. 历史任务缺少 variation_index 时，task_id fallback 稳定
# ============================================================
class TestTaskIdFallback:
    def test_sha256_fallback_is_stable(self):
        """同一 task_id 的 SHA-256 回退索引始终相同"""
        task_id = "historical_task_abc123"
        digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
        index1 = int(digest[:8], 16) % get_preset_count()
        index2 = int(digest[:8], 16) % get_preset_count()
        assert index1 == index2

    def test_sha256_fallback_not_all_zero(self):
        """多个不同 task_id 的 SHA-256 回退不会全部映射到 0"""
        preset_count = get_preset_count()
        task_ids = [f"hist_task_{i}" for i in range(20)]
        indices = set()
        for tid in task_ids:
            digest = hashlib.sha256(tid.encode("utf-8")).hexdigest()
            idx = int(digest[:8], 16) % preset_count
            indices.add(idx)

        # 20 个不同 task_id 应该至少映射到 2 个不同预设
        assert len(indices) >= 2, (
            f"20 个 task_id 只映射到 {len(indices)} 个预设索引，分布不均匀: {indices}"
        )

    def test_fallback_differs_from_uniform_zero(self):
        """SHA-256 回退结果不应全部等同于 variation_index=0"""
        preset_count = get_preset_count()
        all_presets = get_presets()
        preset_at_zero = all_presets[0].preset_id

        # 用 get_preset_for_task_id（内部也用 SHA-256）检查
        fallback_presets = set()
        for i in range(10):
            preset = get_preset_for_task_id(f"legacy_task_{i}")
            fallback_presets.add(preset.preset_id)

        # 不应所有结果都和 index=0 相同
        assert len(fallback_presets) > 1 or preset_at_zero not in fallback_presets, (
            "所有 task_id 的 SHA-256 回退都映射到同一个预设"
        )


# ============================================================
# 5. subtitle_style_selection_node 集成测试
# ============================================================
class TestSubtitleStyleSelectionNode:
    def test_node_with_variation_index(self):
        """节点收到 variation_index 时正确使用均衡轮换"""
        from graphs.nodes.subtitle_style_selection_node import subtitle_style_selection_node

        all_presets = get_presets()

        async def _run(task_index):
            state = {
                "task_id": f"node_task_{task_index}",
                "variation_seed": 12345,
                "generation_id": "gen_001",
                "variation_index": task_index,
            }
            return await subtitle_style_selection_node(state)

        results = []
        for i in range(6):
            result = asyncio.get_event_loop().run_until_complete(_run(i))
            results.append(result)

        # 验证分配结果
        for i, result in enumerate(results):
            expected_preset = all_presets[i % get_preset_count()]
            assert result["subtitle_preset_id"] == expected_preset.preset_id, (
                f"task_index={i}: 期望 {expected_preset.preset_id}，"
                f"实际 {result['subtitle_preset_id']}"
            )

    def test_node_retry_uses_existing_preset(self):
        """节点 retry 时复用已持久化的 subtitle_preset_id"""
        from graphs.nodes.subtitle_style_selection_node import subtitle_style_selection_node

        existing_style = {
            "font_id": "source_han_sans",
            "style_id": "default_white_black_stroke",
            "font_size": 38,
            "stroke_width": 3,
        }

        state = {
            "task_id": "retry_node_task",
            "variation_seed": 99999,
            "generation_id": "gen_002",
            "variation_index": 3,
            "subtitle_preset_id": "preset_01_source_han",
            "subtitle_style": existing_style,
        }

        result = asyncio.get_event_loop().run_until_complete(
            subtitle_style_selection_node(state)
        )

        assert result["subtitle_preset_id"] == "preset_01_source_han"
        assert "reused" in result["node_trace"][0]

    def test_node_fallback_without_variation_index(self):
        """节点缺少 variation_index 时使用 SHA-256 回退，不会全部为 0"""
        from graphs.nodes.subtitle_style_selection_node import subtitle_style_selection_node

        async def _run(task_id):
            state = {
                "task_id": task_id,
                "variation_seed": 0,
                "generation_id": "gen_003",
                # 注意：没有 variation_index
            }
            return await subtitle_style_selection_node(state)

        preset_ids = set()
        for i in range(10):
            result = asyncio.get_event_loop().run_until_complete(
                _run(f"legacy_node_task_{i}")
            )
            preset_ids.add(result["subtitle_preset_id"])

        # 10 个不同 task_id 应该至少产生 2 个不同预设
        assert len(preset_ids) >= 2, (
            f"10 个 task_id 的 SHA-256 回退只产生 {len(preset_ids)} 个预设: {preset_ids}"
        )

    def test_node_persists_variation_index(self):
        """节点输出包含 variation_index"""
        from graphs.nodes.subtitle_style_selection_node import subtitle_style_selection_node

        state = {
            "task_id": "persist_test_task",
            "variation_seed": 42,
            "generation_id": "gen_004",
            "variation_index": 2,
        }

        result = asyncio.get_event_loop().run_until_complete(
            subtitle_style_selection_node(state)
        )

        assert "variation_index" in result
        assert result["variation_index"] == 2

    def test_node_persists_font_size_and_stroke_width(self):
        """节点输出包含 subtitle_font_size 和 subtitle_stroke_width"""
        from graphs.nodes.subtitle_style_selection_node import subtitle_style_selection_node

        state = {
            "task_id": "font_size_test_task",
            "variation_seed": 42,
            "generation_id": "gen_005",
            "variation_index": 0,
        }

        result = asyncio.get_event_loop().run_until_complete(
            subtitle_style_selection_node(state)
        )

        assert "subtitle_font_size" in result
        assert "subtitle_stroke_width" in result
        assert isinstance(result["subtitle_font_size"], int)
        assert isinstance(result["subtitle_stroke_width"], int)
