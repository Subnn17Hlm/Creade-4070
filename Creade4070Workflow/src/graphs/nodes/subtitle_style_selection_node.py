"""
字幕样式选择节点

根据 variation_seed 和 task_id 确定性选择字幕预设，确保：
1. 同一 generation 重试时样式保持一致
2. 不同 generation 可以选择不同样式
3. 同批次内多条任务尽量均衡轮换
"""

import logging
from typing import Any, Dict

from graphs.state import GlobalState
from subtitle_styling import (
    assign_subtitle_style,
    assignment_to_dict,
    get_enabled_fonts,
    get_style_registry,
)

logger = logging.getLogger(__name__)


async def subtitle_style_selection_node(state: GlobalState) -> Dict[str, Any]:
    """
    选择字幕样式预设
    
    根据 variation_seed 和 task_id 确定性选择字幕预设。
    如果已有选择结果（从 output_data 恢复），则复用。
    """
    logger.info("=== 字幕样式选择节点开始 ===")
    
    task_id = state.get("task_id", "")
    variation_seed = state.get("variation_seed", 0)
    generation_id = state.get("generation_id", "")
    
    # 检查是否已有选择结果（从 output_data 恢复）
    existing_style = state.get("subtitle_style")
    existing_preset_id = state.get("subtitle_preset_id")
    
    if existing_style and existing_preset_id:
        logger.info(f"复用已有字幕样式: preset={existing_preset_id}")
        return {
            "subtitle_preset_id": existing_preset_id,
            "subtitle_style": existing_style,
            "subtitle_font_id": existing_style.get("font_id", "source_han_sans"),
            "subtitle_fallback_used": state.get("subtitle_fallback_used", False),
            "node_trace": ["subtitle_style_selection_node:reused"],
        }
    
    # 使用 assign_subtitle_style 进行选择
    # 该函数使用 task_id 作为确定性种子
    if not task_id:
        # 如果没有 task_id，使用 variation_seed 作为后备
        task_id = f"seed_{variation_seed}"
        logger.warning(f"缺少 task_id，使用 variation_seed 作为后备: {task_id}")
    
    try:
        assignment = assign_subtitle_style(task_id)
        style_dict = assignment_to_dict(assignment)
        
        logger.info(f"字幕样式选择完成: preset={assignment.style_id}, font={assignment.font_id}")
        
        return {
            "subtitle_preset_id": assignment.style_id,
            "subtitle_font_id": assignment.font_id,
            "subtitle_style": style_dict,
            "subtitle_fallback_used": assignment.fallback_used,
            "node_trace": ["subtitle_style_selection_node:selected"],
        }
    except Exception as e:
        logger.error(f"字幕样式选择失败: {e}，使用默认样式")
        # 返回默认样式
        default_style = {
            "style_id": "default_white_black_stroke",
            "font_id": "source_han_sans",
            "font_size": 38,
            "stroke_width": 3,
            "stroke_fill": "#000000",
            "text_fill": "#FFFFFF",
            "position_y": 0.85,
            "line_spacing": 1.2,
        }
        return {
            "subtitle_preset_id": "default_white_black_stroke",
            "subtitle_font_id": "source_han_sans",
            "subtitle_style": default_style,
            "subtitle_fallback_used": True,
            "node_trace": ["subtitle_style_selection_node:fallback"],
        }
