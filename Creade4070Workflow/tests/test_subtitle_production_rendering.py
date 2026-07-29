"""
Production-grade subtitle rendering tests.

These tests verify:
1. 4 presets produce 4 different PNGs with different pixel hashes
2. Subtitle block center Y is at 75% of video height (960 for 1280 video)
3. Fallback is properly reported when rendering fails
"""
import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from subtitle_styling import assign_subtitle_style, assignment_to_dict
from subtitle_styling.style_pool import get_style_by_id


def _render_subtitle_png_for_test(
    text: str,
    output_path: str,
    video_width: int,
    video_height: int,
    font_path: str,
    subtitle_style=None,
) -> dict:
    """Render a subtitle PNG using the same logic as final_composition_node."""
    from graphs.nodes.final_composition_node import _render_subtitle_png
    return _render_subtitle_png(
        text=text,
        output_path=output_path,
        video_width=video_width,
        video_height=video_height,
        font_path=font_path,
        subtitle_style=subtitle_style,
    )


def _get_png_pixel_hash(png_path: str) -> str:
    """Get MD5 hash of PNG pixel data."""
    with Image.open(png_path) as img:
        return hashlib.md5(img.tobytes()).hexdigest()


def _get_subtitle_center_y(png_path: str, video_height: int) -> int:
    """
    Calculate the center Y of the subtitle text block.
    
    Returns the Y coordinate of the center of the non-transparent pixels.
    """
    with Image.open(png_path) as img:
        img = img.convert("RGBA")
        pixels = img.load()
        width, height = img.size
        
        # Find all non-transparent pixels
        y_coords = []
        for y in range(height):
            for x in range(width):
                r, g, b, a = pixels[x, y]
                if a > 10:  # Non-transparent
                    y_coords.append(y)
        
        if not y_coords:
            return -1  # No visible pixels
        
        # Calculate center Y
        min_y = min(y_coords)
        max_y = max(y_coords)
        center_y = (min_y + max_y) // 2
        return center_y


class TestProductionSubtitleRendering:
    """Production-grade tests for subtitle rendering."""
    
    def test_four_presets_four_different_pngs(self, tmp_path):
        """
        Test that 4 presets produce 4 visually different PNGs.
        
        This is the core test that would have caught the production issue
        where all 4 tasks rendered the same subtitle style.
        """
        video_width = 720
        video_height = 1280
        text = "这是一段测试字幕文本"
        
        results = []
        png_hashes = []
        center_ys = []
        
        for task_index in range(4):
            # Step 1: Get preset assignment (like subtitle_style_selection_node)
            assignment = assign_subtitle_style(
                task_id=f"test_task_{task_index}",
                task_index=task_index,
            )
            
            # Step 2: Convert to dict (like assignment_to_dict)
            style_dict = assignment_to_dict(assignment)
            
            # Step 3: Recover SubtitleStyle (like resolve_subtitle_render_config)
            style_id = style_dict.get("subtitle_style_id")
            subtitle_style = get_style_by_id(style_id) if style_id else None
            
            # Step 4: Render PNG (like _render_subtitle_png)
            png_path = str(tmp_path / f"subtitle_{task_index}.png")
            font_path = style_dict.get("subtitle_font_path")
            
            render_result = _render_subtitle_png_for_test(
                text=text,
                output_path=png_path,
                video_width=video_width,
                video_height=video_height,
                font_path=font_path,
                subtitle_style=subtitle_style,
            )
            
            # Collect results
            results.append({
                "task_index": task_index,
                "preset_id": assignment.preset_id,
                "style_id": style_id,
                "font_path": font_path,
                "render_fallback_used": render_result.get("render_fallback_used", False),
                "render_fallback_reason": render_result.get("render_fallback_reason"),
            })
            
            # Get PNG hash and center Y
            png_hashes.append(_get_png_pixel_hash(png_path))
            center_ys.append(_get_subtitle_center_y(png_path, video_height))
        
        # Verify 4 different presets
        preset_ids = [r["preset_id"] for r in results]
        assert preset_ids == ["preset_01_source_han", "preset_02_smiley_sans", 
                              "preset_03_alibaba_bold", "preset_04_alibaba_heavy"], \
            f"Expected 4 different presets, got {preset_ids}"
        
        # Verify 4 different style_ids
        style_ids = [r["style_id"] for r in results]
        assert len(set(style_ids)) == 4, \
            f"Expected 4 different style_ids, got {style_ids}"
        
        # Verify no fallback
        for r in results:
            assert not r["render_fallback_used"], \
                f"Task {r['task_index']} should not have fallback: {r['render_fallback_reason']}"
        
        # Verify 4 different PNG hashes
        assert len(set(png_hashes)) == 4, \
            f"Expected 4 different PNG hashes, got {len(set(png_hashes))}: {png_hashes}"
        
        # Verify center Y is at 75% of video height (960 for 1280 video)
        expected_center_y = int(video_height * 0.75)  # 960
        for i, center_y in enumerate(center_ys):
            assert abs(center_y - expected_center_y) <= 2, \
                f"Task {i}: center Y {center_y} not within ±2 of expected {expected_center_y}"
    
    def test_subtitle_center_y_at_75_percent(self, tmp_path):
        """
        Test that subtitle block center is at exactly 75% of video height.
        
        For 720×1280 video, center Y must be 960±2.
        """
        video_width = 720
        video_height = 1280
        text = "测试字幕位置"
        
        # Use preset_01
        assignment = assign_subtitle_style(task_id="test_task", task_index=0)
        style_dict = assignment_to_dict(assignment)
        style_id = style_dict.get("subtitle_style_id")
        subtitle_style = get_style_by_id(style_id)
        
        png_path = str(tmp_path / "subtitle.png")
        font_path = style_dict.get("subtitle_font_path")
        
        _render_subtitle_png_for_test(
            text=text,
            output_path=png_path,
            video_width=video_width,
            video_height=video_height,
            font_path=font_path,
            subtitle_style=subtitle_style,
        )
        
        center_y = _get_subtitle_center_y(png_path, video_height)
        expected_center_y = int(video_height * 0.75)  # 960
        
        assert abs(center_y - expected_center_y) <= 2, \
            f"Center Y {center_y} not within ±2 of expected {expected_center_y}"
    
    def test_fallback_reported_when_font_invalid(self, tmp_path):
        """
        Test that fallback is properly reported when font path is invalid.
        
        This ensures the system cannot silently render with default style
        while reporting fallback=False.
        """
        video_width = 720
        video_height = 1280
        text = "测试回退报告"
        
        # Use preset_01 but with invalid font path
        assignment = assign_subtitle_style(task_id="test_task", task_index=0)
        style_dict = assignment_to_dict(assignment)
        style_id = style_dict.get("subtitle_style_id")
        subtitle_style = get_style_by_id(style_id)
        
        png_path = str(tmp_path / "subtitle.png")
        invalid_font_path = "/nonexistent/font.ttf"
        
        render_result = _render_subtitle_png_for_test(
            text=text,
            output_path=png_path,
            video_width=video_width,
            video_height=video_height,
            font_path=invalid_font_path,
            subtitle_style=subtitle_style,
        )
        
        # Should report fallback
        assert render_result.get("render_fallback_used") is True, \
            "Should report fallback when font path is invalid"
        assert render_result.get("render_fallback_reason") is not None, \
            "Should provide fallback reason"
    
    def test_same_script_different_tasks_different_output(self, tmp_path):
        """
        Test that same script text produces different output for different tasks.
        
        This is the exact scenario from the production issue where 4 tasks
        with similar scripts all rendered the same subtitle style.
        """
        video_width = 720
        video_height = 1280
        # Same script text for all 4 tasks
        text = "这是一段完全相同的测试文案，用于验证不同任务是否产生不同的字幕样式"
        
        png_hashes = []
        
        for task_index in range(4):
            assignment = assign_subtitle_style(
                task_id=f"test_task_{task_index}",
                task_index=task_index,
            )
            style_dict = assignment_to_dict(assignment)
            style_id = style_dict.get("subtitle_style_id")
            subtitle_style = get_style_by_id(style_id)
            
            png_path = str(tmp_path / f"subtitle_{task_index}.png")
            font_path = style_dict.get("subtitle_font_path")
            
            _render_subtitle_png_for_test(
                text=text,
                output_path=png_path,
                video_width=video_width,
                video_height=video_height,
                font_path=font_path,
                subtitle_style=subtitle_style,
            )
            
            png_hashes.append(_get_png_pixel_hash(png_path))
        
        # All 4 PNGs must be different
        assert len(set(png_hashes)) == 4, \
            f"Same script should produce 4 different PNGs for different tasks, got {len(set(png_hashes))} unique"
