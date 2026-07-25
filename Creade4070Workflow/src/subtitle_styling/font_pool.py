"""
字幕字体池模块

管理可用字体配置，提供字体加载、验证和回退能力。
字体路径基于项目根目录解析，不依赖工作目录。
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 项目根目录（src/ 的父目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FONTS_DIR = _PROJECT_ROOT / "assets" / "fonts"


@dataclass(frozen=True)
class FontConfig:
    """单个字体配置"""
    font_id: str
    font_name: str
    font_family: str
    font_weight: str
    font_path: str
    supports_chinese: bool
    license_name: str
    license_path: str
    enabled: bool
    fallback_font_id: str


@dataclass(frozen=True)
class FontLoadResult:
    """字体加载结果"""
    success: bool
    font_config: Optional[FontConfig] = None
    error: Optional[str] = None
    test_width: int = 0
    test_height: int = 0


# 测试文本
_FONT_TEST_TEXT = "字幕字体样式测试"

# 默认回退字体 ID
DEFAULT_FONT_ID = "source_han_sans"
DEFAULT_FONT_WEIGHT = "Regular"


def _build_font_registry() -> List[FontConfig]:
    """
    构建字体注册表。
    按 font_id 字母序排列，保证确定性。
    """
    fonts: List[FontConfig] = []

    # 1. 思源黑体 Regular (NotoSansSC) — OFL 许可证，确认可用
    noto_path = _FONTS_DIR / "NotoSansSC-Regular.otf"
    fonts.append(FontConfig(
        font_id="source_han_sans",
        font_name="思源黑体",
        font_family="Source Han Sans SC",
        font_weight="Regular",
        font_path=str(noto_path),
        supports_chinese=True,
        license_name="SIL Open Font License 1.1",
        license_path="https://scripts.sil.org/OFL",
        enabled=True,
        fallback_font_id="",  # 这是默认回退字体
    ))

    # 2. 阿里巴巴普惠体 Bold — 检测到但许可证未确认
    alibaba_bold_path = _FONTS_DIR / "alibaba_puhuiti" / "AlibabaPuHuiTi-Bold.ttf"
    fonts.append(FontConfig(
        font_id="alibaba_puhuiti",
        font_name="阿里巴巴普惠体",
        font_family="Alibaba PuHuiTi",
        font_weight="Bold",
        font_path=str(alibaba_bold_path),
        supports_chinese=True,
        license_name="未确认",
        license_path="",
        enabled=False,  # 许可证未确认，不启用
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 3. 阿里巴巴普惠体 Heavy — 检测到但许可证未确认
    alibaba_heavy_path = _FONTS_DIR / "alibaba_puhuiti" / "AlibabaPuHuiTi-Heavy.ttf"
    fonts.append(FontConfig(
        font_id="alibaba_puhuiti_heavy",
        font_name="阿里巴巴普惠体",
        font_family="Alibaba PuHuiTi",
        font_weight="Heavy",
        font_path=str(alibaba_heavy_path),
        supports_chinese=True,
        license_name="未确认",
        license_path="",
        enabled=False,  # 许可证未确认，不启用
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 4. 得意黑 — 未找到字体文件
    # smiley_sans_path = _FONTS_DIR / "SmileySans-Oblique.ttf"
    fonts.append(FontConfig(
        font_id="smiley_sans",
        font_name="得意黑",
        font_family="Smiley Sans",
        font_weight="Oblique",
        font_path=str(_FONTS_DIR / "SmileySans-Oblique.ttf"),
        supports_chinese=True,
        license_name="SIL Open Font License 1.1",
        license_path="https://github.com/atelier-anchor/smiley-sans",
        enabled=False,  # 字体文件不存在
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 按 font_id 排序，保证确定性
    fonts.sort(key=lambda f: f.font_id)
    return fonts


# 全局字体注册表（不可变）
_FONT_REGISTRY: List[FontConfig] = _build_font_registry()


def get_font_registry() -> List[FontConfig]:
    """获取完整字体注册表（只读）"""
    return list(_FONT_REGISTRY)


def get_enabled_fonts() -> List[FontConfig]:
    """获取已启用的字体列表，按 font_id 排序"""
    return [f for f in _FONT_REGISTRY if f.enabled]


def get_font_by_id(font_id: str) -> Optional[FontConfig]:
    """根据 font_id 获取字体配置"""
    for f in _FONT_REGISTRY:
        if f.font_id == font_id:
            return f
    return None


def get_default_font() -> FontConfig:
    """获取默认回退字体"""
    font = get_font_by_id(DEFAULT_FONT_ID)
    if font is None or not font.enabled:
        # 如果默认字体不可用，使用第一个启用的字体
        enabled = get_enabled_fonts()
        if enabled:
            return enabled[0]
        raise RuntimeError("没有可用的字体")
    return font


def validate_font(font_config: FontConfig) -> FontLoadResult:
    """
    验证字体是否可用：
    1. 文件存在
    2. Pillow 能加载
    3. 能渲染中文
    4. 宽高有效
    """
    if not os.path.exists(font_config.font_path):
        return FontLoadResult(
            success=False,
            font_config=font_config,
            error=f"字体文件不存在: {font_config.font_path}",
        )

    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(font_config.font_path, 38)
        img = Image.new("RGBA", (720, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), _FONT_TEST_TEXT, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        if width <= 0 or height <= 0:
            return FontLoadResult(
                success=False,
                font_config=font_config,
                error=f"字体渲染宽高无效: width={width}, height={height}",
                test_width=width,
                test_height=height,
            )

        return FontLoadResult(
            success=True,
            font_config=font_config,
            test_width=width,
            test_height=height,
        )
    except Exception as e:
        return FontLoadResult(
            success=False,
            font_config=font_config,
            error=f"字体加载或渲染失败: {e}",
        )


def measure_text_width(
    text: str,
    font_path: str,
    font_size: int,
) -> int:
    """
    使用 Pillow 测量文本在指定字体和字号下的像素宽度。
    支持中文、英文、数字和标点混排。
    """
    from PIL import Image, ImageDraw, ImageFont

    try:
        font = ImageFont.truetype(font_path, font_size)
        img = Image.new("RGBA", (1, 1), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), text, font=font)
        return max(0, bbox[2] - bbox[0])
    except Exception as e:
        logger.warning("测量文本宽度失败: %s, 使用字符数估算", e)
        # 回退：按字符数估算（中文字符按 font_size 宽度，其他按 font_size * 0.6）
        estimated = 0
        for ch in text:
            if '\u4e00' <= ch <= '\u9fff' or '\u3000' <= ch <= '\u303f':
                estimated += font_size
            else:
                estimated += int(font_size * 0.6)
        return estimated


def get_font_status_report() -> Dict[str, any]:
    """获取字体池状态报告"""
    report = {
        "total_fonts": len(_FONT_REGISTRY),
        "enabled_fonts": 0,
        "disabled_fonts": 0,
        "fonts": [],
    }
    for f in _FONT_REGISTRY:
        result = validate_font(f)
        entry = {
            "font_id": f.font_id,
            "font_name": f.font_name,
            "font_weight": f.font_weight,
            "font_path": f.font_path,
            "enabled": f.enabled,
            "file_exists": os.path.exists(f.font_path),
            "loadable": result.success,
            "license_name": f.license_name,
            "error": result.error,
        }
        report["fonts"].append(entry)
        if f.enabled:
            report["enabled_fonts"] += 1
        else:
            report["disabled_fonts"] += 1
    return report
