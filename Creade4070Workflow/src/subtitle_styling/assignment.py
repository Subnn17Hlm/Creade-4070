"""
字幕字体与样式确定性分配模块

使用预设系统实现批次内确定性均衡轮换。
同一 task_id 每次运行结果一致，服务重启后结果一致。
"""
import hashlib
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional

from subtitle_styling.font_pool import (
    DEFAULT_FONT_ID,
    DEFAULT_FONT_WEIGHT,
    FontConfig,
    get_default_font,
    get_enabled_fonts,
    get_font_by_id,
    validate_font,
)
from subtitle_styling.style_pool import (
    DEFAULT_STYLE_ID,
    SubtitleStyle,
    get_default_style,
    get_enabled_styles,
    get_style_by_id,
    validate_style,
)
from subtitle_styling.presets import (
    SubtitlePreset,
    get_preset_by_id,
    get_preset_for_task,
    get_preset_for_task_id,
    get_presets,
    validate_preset,
)

logger = logging.getLogger(__name__)


@dataclass
class SubtitleAssignment:
    """字幕字体与样式分配结果"""
    font_id: str
    font_name: str
    font_weight: str
    font_path: str
    style_id: str
    style_name: str
    style: SubtitleStyle
    font_config: FontConfig
    preset_id: str
    fallback_used: bool
    fallback_reason: str


def _sha256_digest(task_id: str) -> str:
    """计算 task_id 的 SHA-256 摘要（十六进制字符串）"""
    return hashlib.sha256(task_id.encode("utf-8")).hexdigest()


def assign_subtitle_style(
    task_id: str,
    existing_assignment: Optional[Dict] = None,
    task_index: Optional[int] = None,
) -> SubtitleAssignment:
    """
    根据 task_id 或 task_index 确定性分配字幕字体和样式。

    参数:
        task_id: 任务 ID（字符串）
        existing_assignment: 已保存的分配结果（用于任务重试时保持一致）
        task_index: 任务在批次中的索引（用于均衡轮换）

    返回:
        SubtitleAssignment: 分配结果

    算法:
        1. 如果已有保存的分配结果，直接使用
        2. 如果提供 task_index，使用均衡轮换（task_index % preset_count）
        3. 否则，使用 SHA-256(task_id) 计算索引
        4. 从预设列表中选择
        5. 验证选择结果，失败则回退到默认
    """
    # 1. 如果已有保存的分配结果，优先使用
    if existing_assignment:
        return _restore_assignment(existing_assignment)

    # 2. 获取预设列表
    presets = get_presets()
    if not presets:
        logger.warning("没有可用的预设，使用默认回退")
        return _create_fallback_assignment("没有可用的预设")

    # 3. 选择预设
    if task_index is not None:
        # 使用均衡轮换
        preset = get_preset_for_task(task_index)
        logger.info(f"使用均衡轮换: task_index={task_index}, preset={preset.preset_id}")
    else:
        # 使用哈希确定性选择
        preset = get_preset_for_task_id(task_id)
        logger.info(f"使用哈希选择: task_id={task_id}, preset={preset.preset_id}")

    # 4. 验证预设
    valid, error = validate_preset(preset)
    if not valid:
        logger.warning(f"预设 {preset.preset_id} 验证失败: {error}，回退到默认")
        return _create_fallback_assignment(f"预设 {preset.preset_id} 验证失败: {error}")

    # 5. 获取字体和样式
    selected_font = get_font_by_id(preset.font_id)
    selected_style = get_style_by_id(preset.style_id)

    if selected_font is None:
        logger.warning(f"字体 {preset.font_id} 不存在，回退到默认")
        return _create_fallback_assignment(f"字体 {preset.font_id} 不存在")

    if selected_style is None:
        logger.warning(f"样式 {preset.style_id} 不存在，回退到默认")
        return _create_fallback_assignment(f"样式 {preset.style_id} 不存在")

    # 6. 验证字体
    font_validation = validate_font(selected_font)
    if not font_validation.success:
        logger.warning(
            f"字体 {selected_font.font_id} 验证失败: {font_validation.error}，回退到默认字体"
        )
        selected_font = get_default_font()
        fallback_reason = f"字体 {selected_font.font_id} 验证失败: {font_validation.error}"
    else:
        fallback_reason = ""

    return SubtitleAssignment(
        font_id=selected_font.font_id,
        font_name=selected_font.font_name,
        font_weight=selected_font.font_weight,
        font_path=selected_font.font_path,
        style_id=selected_style.style_id,
        style_name=selected_style.style_name,
        style=selected_style,
        font_config=selected_font,
        preset_id=preset.preset_id,
        fallback_used=bool(fallback_reason),
        fallback_reason=fallback_reason,
    )


def _restore_assignment(saved: Dict) -> SubtitleAssignment:
    """从已保存的分配结果恢复"""
    font_id = saved.get("subtitle_font_id", DEFAULT_FONT_ID)
    style_id = saved.get("subtitle_style_id", DEFAULT_STYLE_ID)
    preset_id = saved.get("subtitle_preset_id", "")

    font_config = get_font_by_id(font_id)
    style = get_style_by_id(style_id)

    if font_config is None:
        font_config = get_default_font()
    if style is None:
        style = get_default_style()

    return SubtitleAssignment(
        font_id=font_config.font_id,
        font_name=font_config.font_name,
        font_weight=font_config.font_weight,
        font_path=font_config.font_path,
        style_id=style.style_id,
        style_name=style.style_name,
        style=style,
        font_config=font_config,
        preset_id=preset_id,
        fallback_used=saved.get("subtitle_fallback_used", False),
        fallback_reason=saved.get("subtitle_fallback_reason", ""),
    )


def _create_fallback_assignment(reason: str) -> SubtitleAssignment:
    """创建默认回退分配"""
    font = get_default_font()
    style = get_default_style()
    return SubtitleAssignment(
        font_id=font.font_id,
        font_name=font.font_name,
        font_weight=font.font_weight,
        font_path=font.font_path,
        style_id=style.style_id,
        style_name=style.style_name,
        style=style,
        font_config=font,
        preset_id="fallback_default",
        fallback_used=True,
        fallback_reason=reason,
    )


def assignment_to_dict(assignment: SubtitleAssignment) -> Dict[str, any]:
    """将分配结果转换为可序列化的字典"""
    return {
        "subtitle_font_id": assignment.font_id,
        "subtitle_font_name": assignment.font_name,
        "subtitle_font_weight": assignment.font_weight,
        "subtitle_font_path": assignment.font_path,
        "subtitle_style_id": assignment.style_id,
        "subtitle_style_name": assignment.style_name,
        "subtitle_preset_id": assignment.preset_id,
        "subtitle_fallback_used": assignment.fallback_used,
        "subtitle_fallback_reason": assignment.fallback_reason,
    }
