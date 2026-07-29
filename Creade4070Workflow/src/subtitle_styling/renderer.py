"""
字幕渲染器模块

基于字体池和样式池渲染字幕 PNG。
支持真实像素宽度测量、自动换行、超宽回退。
"""
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

from subtitle_styling.font_pool import measure_text_width, validate_font
from subtitle_styling.style_pool import (
    SAFE_MARGIN_SIDE,
    VIDEO_HEIGHT,
    VIDEO_WIDTH,
    SubtitleStyle,
    get_default_style,
)

logger = logging.getLogger(__name__)

# 安全最小字号
MIN_FONT_SIZE = 20
# 最大行数
MAX_LINES = 2
# 字幕块中心 Y 位置比例（从画面顶部算起 75% 处）
SUBTITLE_Y_RATIO = 0.75


def wrap_text_by_pixel_width(
    text: str,
    font_path: str,
    font_size: int,
    max_width: int,
    max_lines: int = MAX_LINES,
) -> List[str]:
    """
    根据真实像素宽度对文本进行换行。

    参数:
        text: 原始文本
        font_path: 字体文件路径
        font_size: 字号
        max_width: 最大像素宽度
        max_lines: 最大行数

    返回:
        换行后的文本行列表
    """
    # 如果文本已经包含换行符，先按换行符分割
    if '\n' in text:
        lines = text.split('\n')
        # 对每一行再进行像素宽度换行
        result = []
        for line in lines:
            wrapped = _wrap_single_line(line, font_path, font_size, max_width)
            result.extend(wrapped)
        # 限制最大行数
        return result[:max_lines]

    # 单行文本，按像素宽度换行
    lines = _wrap_single_line(text, font_path, font_size, max_width)
    return lines[:max_lines]


def _wrap_single_line(
    text: str,
    font_path: str,
    font_size: int,
    max_width: int,
) -> List[str]:
    """对单行文本按像素宽度换行"""
    if not text:
        return [""]

    # 测量整行宽度
    total_width = measure_text_width(text, font_path, font_size)
    if total_width <= max_width:
        return [text]

    # 需要换行，逐字符累加
    lines = []
    current_line = ""
    current_width = 0

    for char in text:
        char_width = measure_text_width(char, font_path, font_size)
        if current_width + char_width > max_width and current_line:
            lines.append(current_line)
            current_line = char
            current_width = char_width
        else:
            current_line += char
            current_width += char_width

    if current_line:
        lines.append(current_line)

    return lines if lines else [text]


def adjust_font_size_for_width(
    text: str,
    font_path: str,
    font_size: int,
    max_width: int,
    min_font_size: int = MIN_FONT_SIZE,
) -> int:
    """
    如果文本超宽，逐步降低字号直到适配。
    返回调整后的字号（不低于 min_font_size）。
    """
    current_size = font_size
    while current_size > min_font_size:
        width = measure_text_width(text, font_path, current_size)
        if width <= max_width:
            return current_size
        current_size -= 1

    return min_font_size


def render_subtitle_png(
    text: str,
    output_path: str,
    font_path: str,
    style: SubtitleStyle,
    video_width: int = VIDEO_WIDTH,
    video_height: int = VIDEO_HEIGHT,
) -> Dict[str, Any]:
    """
    使用 Pillow 渲染透明 PNG 字幕图层。

    参数:
        text: 字幕文本
        output_path: 输出 PNG 路径
        font_path: 字体文件路径
        style: 字幕样式
        video_width: 视频宽度
        video_height: 视频高度

    返回:
        渲染结果字典
    """
    result = {
        "success": False,
        "text_bbox": None,
        "non_transparent_pixel_count": 0,
        "font_size_used": style.font_size,
        "lines_rendered": 0,
        "error": None,
    }

    try:
        from PIL import Image, ImageDraw, ImageFont

        # 计算最大文本宽度（视频宽度减去左右安全边距）
        max_text_width = video_width - 2 * SAFE_MARGIN_SIDE

        # 根据真实像素宽度换行
        lines = wrap_text_by_pixel_width(
            text, font_path, style.font_size, max_text_width, style.max_chars_per_line
        )

        # 如果换行后行数超过限制，尝试降低字号
        if len(lines) > MAX_LINES:
            # 合并所有行，尝试用更小的字号重新换行
            merged_text = "".join(lines)
            adjusted_size = adjust_font_size_for_width(
                merged_text, font_path, style.font_size, max_text_width
            )
            if adjusted_size < style.font_size:
                logger.info(
                    "文本超宽，字号从 %d 降低到 %d",
                    style.font_size,
                    adjusted_size,
                )
                lines = wrap_text_by_pixel_width(
                    text, font_path, adjusted_size, max_text_width, MAX_LINES
                )
                result["font_size_used"] = adjusted_size

        # 限制最大行数
        lines = lines[:MAX_LINES]
        result["lines_rendered"] = len(lines)

        # 加载字体
        font_size = result["font_size_used"]
        try:
            font = ImageFont.truetype(font_path, font_size)
        except Exception as e:
            result["error"] = f"加载字体失败: {e}"
            result["render_fallback_used"] = True
            result["render_fallback_reason"] = f"font_load_failed: {e}"
            return result

        # 创建透明背景
        img = Image.new("RGBA", (video_width, video_height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # 计算文本位置：字幕块中心固定在画面 75% 高度处
        anchor_y = int(video_height * SUBTITLE_Y_RATIO)
        
        # 使用实际字体度量计算行高（而不是 font_size + line_spacing）
        sample_bbox = draw.textbbox((0, 0), lines[0] if lines else "测", font=font)
        # bbox[1] 是字体的顶部偏移（top bearing），渲染时文本实际从 y + bbox[1] 开始
        top_bearing = sample_bbox[1]
        actual_line_height = sample_bbox[3] - sample_bbox[1]
        line_spacing = max(style.line_spacing, 4)
        
        # 计算背景内边距
        bg_padding = 8 if style.background_enabled else 0
        bg_padding_vertical = bg_padding // 2
        
        # 计算所有行的总高度（包括顶部偏移和背景内边距）
        total_text_height = len(lines) * actual_line_height + (len(lines) - 1) * line_spacing
        # 整个字幕块的高度（包括背景内边距）
        total_block_height = total_text_height + 2 * bg_padding_vertical
        # 调整 start_y，使整个字幕块（包括背景）的中心在 anchor_y
        start_y = anchor_y - total_block_height // 2 + bg_padding_vertical - top_bearing

        all_bbox = []
        for i, line in enumerate(lines):
            # 获取文本边界
            bbox = draw.textbbox((0, 0), line, font=font)
            text_width = bbox[2] - bbox[0]
            text_height = bbox[3] - bbox[1]

            # 居中
            x = (video_width - text_width) // 2
            y = start_y + i * (actual_line_height + line_spacing)

            all_bbox.append((x, y, x + text_width, y + text_height))

            # 绘制背景（如果启用）
            # 注意：文本实际从 y + top_bearing 开始渲染，背景需要覆盖实际文本区域
            if style.background_enabled:
                bg_padding = 8
                bg_rect = (
                    x - bg_padding,
                    y + top_bearing - bg_padding // 2,
                    x + text_width + bg_padding,
                    y + top_bearing + text_height + bg_padding // 2,
                )
                draw.rectangle(bg_rect, fill=style.background_color)

            # 绘制阴影（如果启用）
            if style.shadow_enabled:
                draw.text(
                    (x + style.shadow_offset_x, y + style.shadow_offset_y),
                    line,
                    font=font,
                    fill=style.shadow_color,
                )

            # 绘制描边
            if style.stroke_width > 0:
                outline_width = style.stroke_width
                for dx in range(-outline_width, outline_width + 1):
                    for dy in range(-outline_width, outline_width + 1):
                        if dx != 0 or dy != 0:
                            draw.text(
                                (x + dx, y + dy),
                                line,
                                font=font,
                                fill=style.stroke_color,
                            )

            # 绘制文字
            draw.text((x, y), line, font=font, fill=style.text_color)

        # 保存 PNG
        img.save(output_path, "PNG")

        # 验证文件
        if not os.path.exists(output_path) or os.path.getsize(output_path) == 0:
            result["error"] = "PNG 文件不存在或大小为 0"
            return result

        # 验证 alpha 通道
        img_check = Image.open(output_path)
        alpha = img_check.getchannel("A")
        non_transparent = sum(1 for pixel in alpha.getdata() if pixel > 0)

        result["text_bbox"] = all_bbox
        result["non_transparent_pixel_count"] = non_transparent
        result["success"] = non_transparent > 0

        if non_transparent == 0:
            result["error"] = "PNG 中没有非透明像素"

        return result

    except Exception as e:
        result["error"] = f"渲染失败: {e}"
        logger.error("字幕渲染异常: %s", e, exc_info=True)
        return result


def render_preview_image(
    font_path: str,
    style: SubtitleStyle,
    output_path: str,
    preview_text: str = "字幕字体样式测试 Creade高速吹风机 11万转",
    video_width: int = VIDEO_WIDTH,
    video_height: int = VIDEO_HEIGHT,
) -> Dict[str, Any]:
    """
    渲染预览图，用于测试字体×样式组合。
    """
    return render_subtitle_png(
        text=preview_text,
        output_path=output_path,
        font_path=font_path,
        style=style,
        video_width=video_width,
        video_height=video_height,
    )
