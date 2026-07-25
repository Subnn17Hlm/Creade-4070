"""
字幕样式池模块

定义 12 套字幕样式，包含颜色、描边、阴影、背景等视觉参数。
所有样式针对竖屏 720x1280 视频优化，字幕位于安全区域。
"""
import logging
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SubtitleStyle:
    """字幕样式配置"""
    style_id: str
    style_name: str
    allowed_font_ids: Tuple[str, ...]  # 允许使用的字体 ID
    font_size: int
    text_color: Tuple[int, int, int, int]  # RGBA
    stroke_color: Tuple[int, int, int, int]  # RGBA
    stroke_width: int
    shadow_enabled: bool
    shadow_color: Tuple[int, int, int, int]  # RGBA
    shadow_offset_x: int
    shadow_offset_y: int
    background_enabled: bool
    background_color: Tuple[int, int, int, int]  # RGBA
    background_opacity: float  # 0.0 ~ 1.0
    screen_position: str  # "bottom_center"
    bottom_margin: int  # 像素
    max_chars_per_line: int
    line_spacing: int


# 视频尺寸常量
VIDEO_WIDTH = 720
VIDEO_HEIGHT = 1280
SAFE_MARGIN_BOTTOM = 200  # 底部安全区域（避免遮挡商品区域）
SAFE_MARGIN_SIDE = 40  # 左右安全边距

# 所有字体 ID（用于 allowed_font_ids）
_ALL_FONT_IDS = ("source_han_sans", "alibaba_puhuiti", "alibaba_puhuiti_heavy", "smiley_sans")


def _build_style_registry() -> List[SubtitleStyle]:
    """
    构建 12 套字幕样式注册表。
    按 style_id 排序，保证确定性。
    """
    styles = [
        # 1. 白字黑描边
        SubtitleStyle(
            style_id="default_white_black_stroke",
            style_name="白字黑描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            stroke_width=3,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 2. 黄字黑描边
        SubtitleStyle(
            style_id="yellow_black_stroke",
            style_name="黄字黑描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 220, 0, 255),
            stroke_color=(0, 0, 0, 255),
            stroke_width=3,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 3. 白字半透明黑底
        SubtitleStyle(
            style_id="white_semi_transparent_black_bg",
            style_name="白字半透明黑底",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 255, 255, 255),
            stroke_color=(0, 0, 0, 255),
            stroke_width=1,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=True,
            background_color=(0, 0, 0, 160),
            background_opacity=0.63,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 4. 黄字半透明黑底
        SubtitleStyle(
            style_id="yellow_semi_transparent_black_bg",
            style_name="黄字半透明黑底",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 220, 0, 255),
            stroke_color=(0, 0, 0, 255),
            stroke_width=1,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=True,
            background_color=(0, 0, 0, 160),
            background_opacity=0.63,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 5. 白字蓝色描边
        SubtitleStyle(
            style_id="white_blue_stroke",
            style_name="白字蓝色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 255, 255, 255),
            stroke_color=(30, 90, 200, 255),
            stroke_width=3,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 6. 白字粉色描边
        SubtitleStyle(
            style_id="white_pink_stroke",
            style_name="白字粉色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(255, 255, 255, 255),
            stroke_color=(220, 60, 120, 255),
            stroke_width=3,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 7. 蓝字白色描边（高饱和偏深）
        SubtitleStyle(
            style_id="blue_white_stroke",
            style_name="蓝字白色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(20, 60, 180, 255),
            stroke_color=(255, 255, 255, 255),
            stroke_width=3,
            shadow_enabled=True,
            shadow_color=(0, 0, 0, 100),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 8. 橙字白色描边（高饱和偏深）
        SubtitleStyle(
            style_id="orange_white_stroke",
            style_name="橙字白色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(200, 90, 10, 255),
            stroke_color=(255, 255, 255, 255),
            stroke_width=3,
            shadow_enabled=True,
            shadow_color=(0, 0, 0, 100),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 9. 红字白色描边（高饱和偏深）
        SubtitleStyle(
            style_id="red_white_stroke",
            style_name="红字白色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(180, 20, 20, 255),
            stroke_color=(255, 255, 255, 255),
            stroke_width=3,
            shadow_enabled=True,
            shadow_color=(0, 0, 0, 100),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 10. 绿色字白色描边（高饱和偏深）
        SubtitleStyle(
            style_id="green_white_stroke",
            style_name="绿色字白色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(20, 130, 50, 255),
            stroke_color=(255, 255, 255, 255),
            stroke_width=3,
            shadow_enabled=True,
            shadow_color=(0, 0, 0, 100),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 11. 紫字白色描边（高饱和偏深）
        SubtitleStyle(
            style_id="purple_white_stroke",
            style_name="紫字白色描边",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(120, 30, 160, 255),
            stroke_color=(255, 255, 255, 255),
            stroke_width=3,
            shadow_enabled=True,
            shadow_color=(0, 0, 0, 100),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=False,
            background_color=(0, 0, 0, 0),
            background_opacity=0.0,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
        # 12. 黑字黄色底框
        SubtitleStyle(
            style_id="black_yellow_bg",
            style_name="黑字黄色底框",
            allowed_font_ids=_ALL_FONT_IDS,
            font_size=38,
            text_color=(0, 0, 0, 255),
            stroke_color=(0, 0, 0, 0),
            stroke_width=0,
            shadow_enabled=False,
            shadow_color=(0, 0, 0, 128),
            shadow_offset_x=2,
            shadow_offset_y=2,
            background_enabled=True,
            background_color=(255, 220, 0, 230),
            background_opacity=0.9,
            screen_position="bottom_center",
            bottom_margin=SAFE_MARGIN_BOTTOM,
            max_chars_per_line=16,
            line_spacing=10,
        ),
    ]

    # 按 style_id 排序，保证确定性
    styles.sort(key=lambda s: s.style_id)
    return styles


# 全局样式注册表（不可变）
_STYLE_REGISTRY: List[SubtitleStyle] = _build_style_registry()

# 默认回退样式 ID
DEFAULT_STYLE_ID = "default_white_black_stroke"


def get_style_registry() -> List[SubtitleStyle]:
    """获取完整样式注册表（只读）"""
    return list(_STYLE_REGISTRY)


def get_enabled_styles() -> List[SubtitleStyle]:
    """获取所有样式（全部启用）"""
    return list(_STYLE_REGISTRY)


def get_style_by_id(style_id: str) -> Optional[SubtitleStyle]:
    """根据 style_id 获取样式配置"""
    for s in _STYLE_REGISTRY:
        if s.style_id == style_id:
            return s
    return None


def get_default_style() -> SubtitleStyle:
    """获取默认回退样式"""
    style = get_style_by_id(DEFAULT_STYLE_ID)
    if style is None:
        # 如果默认样式不存在，使用第一个
        return _STYLE_REGISTRY[0]
    return style


def validate_style(style: SubtitleStyle) -> List[str]:
    """
    验证样式配置是否合法。
    返回错误列表，空列表表示合法。
    """
    errors = []

    # font_size 安全范围
    if not (12 <= style.font_size <= 120):
        errors.append(f"font_size={style.font_size} 不在安全范围 [12, 120]")

    # stroke_width 非负
    if style.stroke_width < 0:
        errors.append(f"stroke_width={style.stroke_width} 不能为负")

    # opacity 范围
    if not (0.0 <= style.background_opacity <= 1.0):
        errors.append(f"background_opacity={style.background_opacity} 不在 [0, 1]")

    # 颜色值合法 (RGBA 各通道 0-255)
    for name, color in [
        ("text_color", style.text_color),
        ("stroke_color", style.stroke_color),
        ("shadow_color", style.shadow_color),
        ("background_color", style.background_color),
    ]:
        if len(color) != 4:
            errors.append(f"{name} 必须是 4 元组 RGBA")
        else:
            for i, ch in enumerate(color):
                if not (0 <= ch <= 255):
                    errors.append(f"{name}[{i}]={ch} 不在 [0, 255]")

    # screen_position 合法
    valid_positions = ("bottom_center", "top_center", "center")
    if style.screen_position not in valid_positions:
        errors.append(f"screen_position={style.screen_position} 不合法")

    # bottom_margin 安全范围
    if not (0 <= style.bottom_margin <= 600):
        errors.append(f"bottom_margin={style.bottom_margin} 不在安全范围 [0, 600]")

    # max_chars_per_line > 0
    if style.max_chars_per_line <= 0:
        errors.append(f"max_chars_per_line={style.max_chars_per_line} 必须大于 0")

    return errors


def get_style_status_report() -> Dict[str, any]:
    """获取样式池状态报告"""
    report = {
        "total_styles": len(_STYLE_REGISTRY),
        "styles": [],
    }
    for s in _STYLE_REGISTRY:
        errors = validate_style(s)
        entry = {
            "style_id": s.style_id,
            "style_name": s.style_name,
            "valid": len(errors) == 0,
            "errors": errors,
        }
        report["styles"].append(entry)
    return report
