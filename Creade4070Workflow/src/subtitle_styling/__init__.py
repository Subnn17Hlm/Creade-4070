"""
字幕字体池与样式池模块

提供字幕字体管理、样式定义、确定性分配和渲染能力。
"""
from subtitle_styling.assignment import (
    SubtitleAssignment,
    assign_subtitle_style,
    assignment_to_dict,
)
from subtitle_styling.config_validator import validate_all_configurations
from subtitle_styling.font_pool import (
    DEFAULT_FONT_ID,
    DEFAULT_FONT_WEIGHT,
    FontConfig,
    FontLoadResult,
    get_default_font,
    get_enabled_fonts,
    get_font_by_id,
    get_font_registry,
    get_font_status_report,
    measure_text_width,
    validate_font,
)
from subtitle_styling.presets import (
    SubtitlePreset,
    get_preset_by_id,
    get_preset_count,
    get_preset_for_task,
    get_preset_for_task_id,
    get_preset_status_report,
    get_presets,
    validate_preset,
)
from subtitle_styling.renderer import (
    adjust_font_size_for_width,
    render_preview_image,
    render_subtitle_png,
    wrap_text_by_pixel_width,
)
from subtitle_styling.style_pool import (
    DEFAULT_STYLE_ID,
    SubtitleStyle,
    get_default_style,
    get_enabled_styles,
    get_style_by_id,
    get_style_registry,
    get_style_status_report,
    validate_style,
)

__all__ = [
    # Font pool
    "FontConfig",
    "FontLoadResult",
    "DEFAULT_FONT_ID",
    "DEFAULT_FONT_WEIGHT",
    "get_font_registry",
    "get_enabled_fonts",
    "get_font_by_id",
    "get_default_font",
    "validate_font",
    "measure_text_width",
    "get_font_status_report",
    # Style pool
    "SubtitleStyle",
    "DEFAULT_STYLE_ID",
    "get_style_registry",
    "get_enabled_styles",
    "get_style_by_id",
    "get_default_style",
    "validate_style",
    "get_style_status_report",
    # Presets
    "SubtitlePreset",
    "get_presets",
    "get_preset_by_id",
    "get_preset_count",
    "get_preset_for_task",
    "get_preset_for_task_id",
    "validate_preset",
    "get_preset_status_report",
    # Assignment
    "SubtitleAssignment",
    "assign_subtitle_style",
    "assignment_to_dict",
    # Renderer
    "render_subtitle_png",
    "render_preview_image",
    "wrap_text_by_pixel_width",
    "adjust_font_size_for_width",
    # Validator
    "validate_all_configurations",
]
