"""
字幕样式配置校验模块

在服务启动或测试阶段校验字体池和样式池配置。
非法配置不导致视频任务失败，而是禁用对应字体/样式并记录原因。
"""
import logging
from typing import Dict, List

from subtitle_styling.font_pool import (
    get_enabled_fonts,
    get_font_registry,
    validate_font,
)
from subtitle_styling.style_pool import (
    get_style_registry,
    validate_style,
)

logger = logging.getLogger(__name__)


def validate_all_configurations() -> Dict[str, any]:
    """
    校验所有字体和样式配置。
    返回校验报告。
    """
    report = {
        "valid": True,
        "font_issues": [],
        "style_issues": [],
        "summary": "",
    }

    # 1. 校验字体
    font_ids = set()
    enabled_count = 0
    for font in get_font_registry():
        # 检查 font_id 唯一性
        if font.font_id in font_ids:
            report["font_issues"].append(f"font_id 重复: {font.font_id}")
            report["valid"] = False
        font_ids.add(font.font_id)

        if font.enabled:
            # 验证字体可用性
            result = validate_font(font)
            if not result.success:
                report["font_issues"].append(
                    f"字体 {font.font_id} 验证失败: {result.error}"
                )
                report["valid"] = False
            else:
                enabled_count += 1

    # 至少 1 种启用字体
    if enabled_count == 0:
        report["font_issues"].append("没有可用的字体（至少需要 1 种）")
        report["valid"] = False

    # 2. 校验样式
    style_ids = set()
    for style in get_style_registry():
        # 检查 style_id 唯一性
        if style.style_id in style_ids:
            report["style_issues"].append(f"style_id 重复: {style.style_id}")
            report["valid"] = False
        style_ids.add(style.style_id)

        # 验证样式配置
        errors = validate_style(style)
        if errors:
            report["style_issues"].append(
                f"样式 {style.style_id} 验证失败: {errors}"
            )
            report["valid"] = False

    # 3. 生成摘要
    total_fonts = len(get_font_registry())
    total_styles = len(get_style_registry())
    report["summary"] = (
        f"字体: {enabled_count}/{total_fonts} 启用, "
        f"样式: {total_styles}/{total_styles} 可用"
    )

    if report["font_issues"]:
        logger.warning("字体配置问题: %s", report["font_issues"])
    if report["style_issues"]:
        logger.warning("样式配置问题: %s", report["style_issues"])

    return report
