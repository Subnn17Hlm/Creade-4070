"""
端到端字幕渲染测试

验证完整链路：
subtitle_style_selection_node → resolve_subtitle_render_config → _render_subtitle_png → renderer

用4个不同 batch_task_index 生成实际字幕PNG，验证4张图片的像素哈希至少有4个不同值。
同时验证相同文案的不同任务产生不同预设和不同PNG。
"""
import hashlib
import os
import tempfile

import pytest

from graphs.nodes.final_composition_node import (
    _render_subtitle_png,
    resolve_subtitle_render_config,
)
from subtitle_styling import assign_subtitle_style, assignment_to_dict
from subtitle_styling.style_pool import SubtitleStyle, get_style_by_id


def _png_pixel_hash(path: str) -> str:
    """Compute a hash of the PNG file's raw bytes."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()[:16]


class TestEndToEndSubtitleRendering:
    """端到端渲染测试：从选择到PNG像素级验证。"""

    def test_four_presets_produce_four_different_pngs(self, tmp_path):
        """4个不同 batch_task_index 必须产生4个不同的字幕PNG。"""
        png_hashes = set()
        preset_ids = []
        style_ids = []

        for i in range(4):
            # Step 1: selection node output
            assignment = assign_subtitle_style(f"task_e2e_{i}", task_index=i)
            style_dict = assignment_to_dict(assignment)
            preset_ids.append(assignment.preset_id)
            style_ids.append(assignment.style_id)

            # Step 2: resolve_subtitle_render_config
            state = {
                "subtitle_style": style_dict,
                "subtitle_preset_id": assignment.preset_id,
            }
            font_path, subtitle_style, metadata = resolve_subtitle_render_config(state)

            # Verify SubtitleStyle object recovered
            assert isinstance(subtitle_style, SubtitleStyle), (
                f"task_index={i}: expected SubtitleStyle, got {type(subtitle_style)}"
            )
            assert metadata["subtitle_preset_id"] == assignment.preset_id
            assert metadata["subtitle_style_id"] == assignment.style_id

            # Step 3: render PNG
            png_path = str(tmp_path / f"subtitle_{i}.png")
            result = _render_subtitle_png(
                "测试字幕文本",
                png_path,
                font_path,
                font_size=subtitle_style.font_size,
                video_width=720,
                video_height=1280,
                subtitle_style=subtitle_style,
            )

            assert result["success"], f"task_index={i}: render failed: {result.get('error')}"
            assert os.path.exists(png_path), f"task_index={i}: PNG not created"

            png_hashes.add(_png_pixel_hash(png_path))

        # All 4 presets must be different
        assert len(set(preset_ids)) == 4, f"Expected 4 different presets, got {preset_ids}"
        # All 4 style_ids must be different
        assert len(set(style_ids)) == 4, f"Expected 4 different style_ids, got {style_ids}"
        # All 4 PNG hashes must be different
        assert len(png_hashes) == 4, (
            f"Expected 4 different PNG hashes, got {len(png_hashes)} unique. "
            f"presets={preset_ids}, style_ids={style_ids}"
        )

    def test_same_script_different_tasks_different_output(self, tmp_path):
        """相同文案的不同任务必须产生不同预设和不同PNG。"""
        script_text = "这是一段完全相同的测试文案"
        png_hashes = []
        preset_ids = []

        for i in range(4):
            assignment = assign_subtitle_style(f"same_script_task_{i}", task_index=i)
            preset_ids.append(assignment.preset_id)

            style_dict = assignment_to_dict(assignment)
            state = {
                "subtitle_style": style_dict,
                "subtitle_preset_id": assignment.preset_id,
            }
            font_path, subtitle_style, _ = resolve_subtitle_render_config(state)

            png_path = str(tmp_path / f"same_script_{i}.png")
            result = _render_subtitle_png(
                script_text,
                png_path,
                font_path,
                font_size=subtitle_style.font_size,
                video_width=720,
                video_height=1280,
                subtitle_style=subtitle_style,
            )

            assert result["success"]
            png_hashes.append(_png_pixel_hash(png_path))

        # Different presets
        assert len(set(preset_ids)) == 4, f"Same script got same presets: {preset_ids}"
        # Different PNG hashes
        assert len(set(png_hashes)) == 4, (
            f"Same script produced identical PNGs. presets={preset_ids}"
        )

    def test_renderer_receives_correct_style_properties(self, tmp_path):
        """验证渲染器实际使用了不同样式的属性（颜色、描边等）。"""
        styles_seen = []

        for i in range(4):
            assignment = assign_subtitle_style(f"prop_task_{i}", task_index=i)
            style_dict = assignment_to_dict(assignment)
            state = {"subtitle_style": style_dict, "subtitle_preset_id": assignment.preset_id}
            font_path, subtitle_style, _ = resolve_subtitle_render_config(state)

            styles_seen.append({
                "preset_id": assignment.preset_id,
                "style_id": assignment.style_id,
                "font_path": font_path,
                "font_size": subtitle_style.font_size,
                "text_color": subtitle_style.text_color,
                "stroke_color": subtitle_style.stroke_color,
                "stroke_width": subtitle_style.stroke_width,
                "background_enabled": subtitle_style.background_enabled,
            })

        # Verify that at least text_color or stroke_color differs across presets
        text_colors = set(s["text_color"] for s in styles_seen)
        stroke_colors = set(s["stroke_color"] for s in styles_seen)
        font_paths = set(s["font_path"] for s in styles_seen)

        # At least one visual property must differ
        assert len(text_colors) > 1 or len(stroke_colors) > 1 or len(font_paths) > 1, (
            f"All 4 presets have identical visual properties: {styles_seen}"
        )

    def test_fallback_when_style_id_missing(self, tmp_path):
        """当 subtitle_style_id 缺失时，应回退到默认渲染。"""
        state = {
            "subtitle_style": {"subtitle_font_path": "/nonexistent/font.ttf"},
            "subtitle_preset_id": "default",
        }
        font_path, subtitle_style, metadata = resolve_subtitle_render_config(state)

        # No style_id → subtitle_style should be None
        assert subtitle_style is None
        assert metadata["subtitle_fallback_used"] is True
        # Font path should fall back to a system font
        assert font_path is not None
