"""
字幕样式选择节点

根据 variation_index（批次行号）和 task_id 确定性选择字幕预设，确保：
1. 同一 generation 重试时样式保持一致（读取已持久化的 subtitle_preset_id）
2. 不同 generation 可以选择不同样式
3. 同批次内多条任务通过 batch_task_index 均衡轮换
4. 历史任务缺失 variation_index 时，使用 task_id 的 SHA-256 稳定回退
"""

import hashlib
import logging
from typing import Any, Dict, Optional

from graphs.state import GlobalState
from subtitle_styling import (
    assign_subtitle_style,
    assignment_to_dict,
    get_enabled_fonts,
    get_preset_count,
    get_style_registry,
)

logger = logging.getLogger(__name__)


def _stable_index_from_task_id(task_id: str) -> int:
    """使用 SHA-256(task_id) 计算稳定的预设索引（禁止使用 Python 内置 hash）。"""
    digest = hashlib.sha256(task_id.encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


async def subtitle_style_selection_node(state: GlobalState) -> Dict[str, Any]:
    """
    选择字幕样式预设

    优先级:
    1. 已持久化的 subtitle_preset_id（retry 保持一致）
    2. state 中显式传入的 variation_index（来自 batch_task_index）
    3. task_id 的 SHA-256 稳定回退（历史任务兼容）
    """
    logger.info("=== 字幕样式选择节点开始 ===")

    task_id = state.get("task_id", "")
    variation_seed = state.get("variation_seed", 0)
    generation_id = state.get("generation_id", "")

    # ── 优先级 1：复用已持久化的结果（retry 一致性） ──
    existing_style = state.get("subtitle_style")
    existing_preset_id = state.get("subtitle_preset_id")

    if existing_style and existing_preset_id:
        logger.info(f"复用已有字幕样式: preset={existing_preset_id}")
        # 从持久化字典读取统一字段名
        existing_style_id = existing_style.get("subtitle_style_id", "")
        existing_font_id = existing_style.get("subtitle_font_id", "source_han_sans")
        # 通过 get_style_by_id 恢复完整 SubtitleStyle 对象获取 font_size / stroke_width
        existing_style_obj = get_style_by_id(existing_style_id) if existing_style_id else None
        font_size = getattr(existing_style_obj, "font_size", 38) if existing_style_obj else 38
        stroke_width = getattr(existing_style_obj, "stroke_width", 3) if existing_style_obj else 3
        logger.info(
            f"复用样式详情: style_id={existing_style_id}, font_id={existing_font_id}, "
            f"font_size={font_size}, stroke_width={stroke_width}"
        )
        return {
            "subtitle_preset_id": existing_preset_id,
            "subtitle_style": existing_style,
            "subtitle_font_id": existing_font_id,
            "subtitle_font_size": font_size,
            "subtitle_stroke_width": stroke_width,
            "subtitle_fallback_used": state.get("subtitle_fallback_used", False),
            "variation_index": state.get("variation_index", 0),
            "node_trace": ["subtitle_style_selection_node:reused"],
        }

    # ── 准备 task_id ──
    if not task_id:
        task_id = f"seed_{variation_seed}"
        logger.warning(f"缺少 task_id，使用 variation_seed 作为后备: {task_id}")

    # ── 优先级 2/3：确定 variation_index ──
    # 使用 None 哨兵区分"显式传入 0"和"未传入"
    variation_index: Optional[int] = state.get("variation_index")

    if variation_index is not None:
        # 显式传入（来自 batch_task_index），用于均衡轮换
        logger.info(f"使用 batch_task_index 作为 variation_index: {variation_index}")
        task_index_for_assignment = variation_index
    else:
        # 历史任务缺失 variation_index → SHA-256 稳定回退
        preset_count = get_preset_count()
        if preset_count > 0 and task_id:
            stable_hash = _stable_index_from_task_id(task_id)
            variation_index = stable_hash % preset_count
            logger.info(
                f"variation_index 缺失，使用 SHA-256 回退: "
                f"task_id={task_id}, hash={stable_hash}, "
                f"preset_count={preset_count}, variation_index={variation_index}"
            )
        else:
            variation_index = 0
            logger.warning(f"无法计算稳定回退索引（task_id 或 preset_count 为空），使用 0")

        # 不传 task_index 给 assign_subtitle_style，让它走内部的 hash 路径
        task_index_for_assignment = None

    try:
        assignment = assign_subtitle_style(
            task_id,
            task_index=task_index_for_assignment,
        )
        style_dict = assignment_to_dict(assignment)

        # 从 style 对象提取 font_size 和 stroke_width
        font_size = getattr(assignment.style, "font_size", 38)
        stroke_width = getattr(assignment.style, "stroke_width", 3)

        # 生产日志：完整输出样式选择详情
        logger.info(
            f"[字幕样式选择] batch_task_index={state.get('batch_task_index')}, "
            f"variation_index={variation_index}, "
            f"subtitle_preset_id={assignment.preset_id}, "
            f"subtitle_style_id={assignment.style_id}, "
            f"subtitle_font_id={assignment.font_id}, "
            f"subtitle_font_path={assignment.font_path}, "
            f"font_size={font_size}, "
            f"text_color={getattr(assignment.style, 'text_color', None)}, "
            f"stroke_color={getattr(assignment.style, 'stroke_color', None)}, "
            f"stroke_width={stroke_width}, "
            f"shadow_enabled={getattr(assignment.style, 'shadow_enabled', None)}, "
            f"background_enabled={getattr(assignment.style, 'background_enabled', None)}, "
            f"fallback_used={assignment.fallback_used}, "
            f"fallback_reason={assignment.fallback_reason}"
        )

        return {
            "subtitle_preset_id": assignment.preset_id,
            "subtitle_font_id": assignment.font_id,
            "subtitle_font_size": font_size,
            "subtitle_stroke_width": stroke_width,
            "subtitle_style": style_dict,
            "subtitle_fallback_used": assignment.fallback_used,
            "variation_index": variation_index,
            "node_trace": ["subtitle_style_selection_node:selected"],
        }
    except Exception as e:
        logger.error(f"字幕样式选择失败: {e}，使用默认样式")
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
            "subtitle_font_size": 38,
            "subtitle_stroke_width": 3,
            "subtitle_style": default_style,
            "subtitle_fallback_used": True,
            "variation_index": variation_index if variation_index is not None else 0,
            "node_trace": ["subtitle_style_selection_node:fallback"],
        }
