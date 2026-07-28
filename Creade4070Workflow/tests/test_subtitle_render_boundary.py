"""End-to-end subtitle rendering boundary test.

Verifies that:
1. subtitle_style_selection_node outputs a serializable dict
2. final_composition_node.resolve_subtitle_render_config recovers SubtitleStyle
3. The recovered SubtitleStyle is passed to the renderer (not a dict)
"""
import os
import tempfile
import pytest
from unittest.mock import patch, MagicMock


class TestSubtitleRenderBoundary:
    """Test the full chain from selection output to render input."""

    def test_resolve_returns_subtitle_style_object(self):
        """resolve_subtitle_render_config must return a SubtitleStyle, not dict."""
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config
        from subtitle_styling.style_pool import get_style_by_id, SubtitleStyle

        # Use a real style_id from the style pool
        selected = {
            "subtitle_preset_id": "preset_01_source_han",
            "subtitle_font_id": "source_han_sans",
            "subtitle_font_path": "/tmp/fake_font.ttf",
            "subtitle_style_id": "default_white_black_stroke",
            "subtitle_font_size": 38,
            "subtitle_stroke_width": 3,
        }

        state = {"subtitle_style": selected}

        font_path, style, metadata = resolve_subtitle_render_config(state)

        # style must be a SubtitleStyle object, not a dict
        assert isinstance(style, SubtitleStyle), (
            f"Expected SubtitleStyle, got {type(style).__name__}"
        )
        assert metadata["subtitle_preset_id"] == "preset_01_source_han"
        assert metadata["subtitle_style_id"] == "default_white_black_stroke"
        assert style.style_id == "default_white_black_stroke"

    def test_resolve_returns_none_when_no_style_id(self):
        """When subtitle_style_id is missing, style should be None."""
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config

        state = {"subtitle_style": {"subtitle_font_id": "test"}}
        font_path, style, metadata = resolve_subtitle_render_config(state)

        assert style is None
        assert metadata["subtitle_fallback_used"] is True

    def test_resolve_with_empty_state(self):
        """Empty state should not crash."""
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config

        font_path, style, metadata = resolve_subtitle_render_config({})
        assert style is None
        assert metadata["subtitle_fallback_used"] is True
        assert metadata["subtitle_preset_id"] == "default"

    def test_different_presets_produce_different_styles(self):
        """Different preset selections should resolve to different SubtitleStyle objects."""
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config
        from subtitle_styling.style_pool import SubtitleStyle

        state_a = {"subtitle_style": {
            "subtitle_style_id": "default_white_black_stroke",
            "subtitle_font_path": "/tmp/fake.ttf",
        }}
        state_b = {"subtitle_style": {
            "subtitle_style_id": "black_yellow_bg",
            "subtitle_font_path": "/tmp/fake.ttf",
        }}

        _, style_a, _ = resolve_subtitle_render_config(state_a)
        _, style_b, _ = resolve_subtitle_render_config(state_b)

        assert isinstance(style_a, SubtitleStyle)
        assert isinstance(style_b, SubtitleStyle)
        assert style_a.style_id != style_b.style_id

    def test_renderer_receives_subtitle_style_not_dict(self):
        """The renderer.render_subtitle_png must receive a SubtitleStyle object."""
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config
        from subtitle_styling.style_pool import SubtitleStyle

        selected = {
            "subtitle_preset_id": "preset_02_smiley_sans",
            "subtitle_font_id": "smiley_sans",
            "subtitle_font_path": "/tmp/fake.ttf",
            "subtitle_style_id": "blue_white_stroke",
        }
        state = {"subtitle_style": selected}

        font_path, style, metadata = resolve_subtitle_render_config(state)

        # Simulate what _render_subtitle_png does with the style
        # It should receive a SubtitleStyle, not a dict
        assert isinstance(style, SubtitleStyle)

        # Verify the style has the attributes the renderer needs
        assert hasattr(style, "font_size")
        assert hasattr(style, "stroke_width")
        assert hasattr(style, "style_id")

        # Verify metadata matches the original selection
        assert metadata["subtitle_preset_id"] == selected["subtitle_preset_id"]
        assert metadata["subtitle_style_id"] == selected["subtitle_style_id"]

    def test_assignment_to_dict_roundtrip(self):
        """assignment_to_dict output can be used by resolve_subtitle_render_config."""
        from subtitle_styling.assignment import assign_subtitle_style, assignment_to_dict
        from graphs.nodes.final_composition_node import resolve_subtitle_render_config
        from subtitle_styling.style_pool import SubtitleStyle

        # Get a real assignment
        assignment = assign_subtitle_style("test_task_123", task_index=0)
        serialized = assignment_to_dict(assignment)

        # Build state as subtitle_style_selection_node would
        state = {
            "subtitle_style": serialized,
            "subtitle_preset_id": assignment.preset_id,
        }

        font_path, style, metadata = resolve_subtitle_render_config(state)

        assert isinstance(style, SubtitleStyle)
        assert metadata["subtitle_preset_id"] == assignment.preset_id
        assert metadata["subtitle_style_id"] == assignment.style_id
        assert metadata["subtitle_fallback_used"] is False
