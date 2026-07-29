"""
字幕字体池模块

管理可用字体配置，提供字体加载、验证和回退能力。
字体路径基于项目根目录解析，不依赖工作目录。
字体目录为 assets/Fonts/（大写 F），子目录可能使用非 UTF-8 编码命名，
因此采用递归文件名搜索来定位字体文件。
"""
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

# 项目根目录（src/ 的父目录）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_FONTS_DIR = _PROJECT_ROOT / "assets" / "Fonts"  # 大写 F


def _find_font_file(filename: str) -> Optional[Path]:
    """
    在 _FONTS_DIR 下递归搜索指定文件名的字体文件。
    文件名匹配不区分大小写。
    返回找到的第一个匹配文件的绝对路径，未找到返回 None。
    
    使用 os.walk 处理可能的非 UTF-8 目录名（如 GBK 编码）。
    """
    if not _FONTS_DIR.is_dir():
        logger.warning(
            "[FontPool] 字体目录不存在: %s (exists=%s)",
            _FONTS_DIR, _FONTS_DIR.exists(),
        )
        return None

    filename_lower = filename.lower()

    # 使用 os.walk + bytes 路径处理非 UTF-8 目录名
    fonts_dir_bytes = os.fsencode(str(_FONTS_DIR))
    for dirpath_bytes, _dirnames, filenames_bytes in os.walk(fonts_dir_bytes):
        for fn_bytes in filenames_bytes:
            try:
                fn_str = fn_bytes.decode("utf-8")
            except UnicodeDecodeError:
                try:
                    fn_str = fn_bytes.decode("gbk")
                except UnicodeDecodeError:
                    fn_str = fn_bytes.decode("latin-1")
            if fn_str.lower() == filename_lower:
                full_path_bytes = os.path.join(dirpath_bytes, fn_bytes)
                # 用 surrogateescape 解码为 str，保持与 OS 交互的能力
                try:
                    full_path = full_path_bytes.decode("utf-8")
                except UnicodeDecodeError:
                    full_path = full_path_bytes.decode("utf-8", errors="surrogateescape")
                if os.path.isfile(full_path) and os.path.getsize(full_path) > 0:
                    return Path(full_path)
    return None


def _to_pil_font_path(font_path: str):
    """
    将字体路径转换为 PIL ImageFont.truetype 可接受的格式。
    
    当路径包含 surrogate 字符（来自 GBK 等非 UTF-8 编码目录名）时，
    PIL 无法直接处理 str 路径（会抛出 UnicodeEncodeError）。
    此时将路径编码回 bytes，PIL 可以接受 bytes 路径。
    
    返回 str 或 bytes。
    """
    try:
        # 尝试正常 UTF-8 编码
        font_path.encode("utf-8")
        return font_path
    except UnicodeEncodeError:
        # 路径包含 surrogate 字符，转回 bytes
        return font_path.encode("utf-8", errors="surrogateescape")


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
    使用 _find_font_file 递归搜索 assets/Fonts/ 下的字体文件，
    兼容 GBK 等非 UTF-8 编码的子目录名。
    """
    fonts: List[FontConfig] = []

    # 1. 思源黑体 / NotoSansSC — 搜索 SOURCEHANSERIFCN 或 NotoSansSC
    source_han_path = _find_font_file("SOURCEHANSERIFCN-BOLD.OTF") or _find_font_file("NotoSansSC-Regular.otf")
    source_han_enabled = source_han_path is not None and source_han_path.exists()
    if source_han_path:
        logger.info("[FontPool] source_han_sans 字体路径: %s (exists=%s, size=%d)",
                    source_han_path, source_han_path.exists(),
                    source_han_path.stat().st_size if source_han_path.exists() else 0)
    else:
        logger.warning("[FontPool] source_han_sans 字体未找到 (搜索 SOURCEHANSERIFCN-BOLD.OTF / NotoSansSC-Regular.otf)")
    fonts.append(FontConfig(
        font_id="source_han_sans",
        font_name="思源宋体" if source_han_path and "SOURCEHAN" in source_han_path.name.upper() else "思源黑体",
        font_family="Source Han Serif CN" if source_han_path and "SOURCEHAN" in source_han_path.name.upper() else "Source Han Sans SC",
        font_weight="Bold" if source_han_path and "BOLD" in source_han_path.name.upper() else "Regular",
        font_path=str(source_han_path) if source_han_path else "",
        supports_chinese=True,
        license_name="SIL Open Font License 1.1",
        license_path="https://scripts.sil.org/OFL",
        enabled=source_han_enabled,
        fallback_font_id="",  # 这是默认回退字体
    ))

    # 2. 阿里巴巴普惠体 Bold — 官方免费商用字体
    alibaba_bold_path = _find_font_file("ALIBABA-PUHUITI-BOLD.TTF")
    alibaba_bold_enabled = alibaba_bold_path is not None and alibaba_bold_path.exists()
    if alibaba_bold_path:
        logger.info("[FontPool] alibaba_puhuiti 字体路径: %s (exists=%s, size=%d)",
                    alibaba_bold_path, alibaba_bold_path.exists(),
                    alibaba_bold_path.stat().st_size if alibaba_bold_path.exists() else 0)
    else:
        logger.warning("[FontPool] alibaba_puhuiti 字体未找到 (搜索 ALIBABA-PUHUITI-BOLD.TTF)")
    fonts.append(FontConfig(
        font_id="alibaba_puhuiti",
        font_name="阿里巴巴普惠体",
        font_family="Alibaba PuHuiTi",
        font_weight="Bold",
        font_path=str(alibaba_bold_path) if alibaba_bold_path else "",
        supports_chinese=True,
        license_name="阿里巴巴普惠体免费商用授权",
        license_path="",
        enabled=alibaba_bold_enabled,
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 3. 阿里巴巴普惠体 Heavy — 官方免费商用字体
    alibaba_heavy_path = _find_font_file("ALIBABA-PUHUITI-HEAVY.TTF")
    alibaba_heavy_enabled = alibaba_heavy_path is not None and alibaba_heavy_path.exists()
    if alibaba_heavy_path:
        logger.info("[FontPool] alibaba_puhuiti_heavy 字体路径: %s (exists=%s, size=%d)",
                    alibaba_heavy_path, alibaba_heavy_path.exists(),
                    alibaba_heavy_path.stat().st_size if alibaba_heavy_path.exists() else 0)
    else:
        logger.warning("[FontPool] alibaba_puhuiti_heavy 字体未找到 (搜索 ALIBABA-PUHUITI-HEAVY.TTF)")
    fonts.append(FontConfig(
        font_id="alibaba_puhuiti_heavy",
        font_name="阿里巴巴普惠体",
        font_family="Alibaba PuHuiTi",
        font_weight="Heavy",
        font_path=str(alibaba_heavy_path) if alibaba_heavy_path else "",
        supports_chinese=True,
        license_name="阿里巴巴普惠体免费商用授权",
        license_path="",
        enabled=alibaba_heavy_enabled,
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 4. 得意黑 (Smiley Sans) — 搜索 SmileySans 或 优设标题黑
    smiley_path = _find_font_file("SmileySans-Oblique.ttf") or _find_font_file("优设标题黑.TTF")
    smiley_enabled = smiley_path is not None and smiley_path.exists()
    if smiley_path:
        logger.info("[FontPool] smiley_sans 字体路径: %s (exists=%s, size=%d)",
                    smiley_path, smiley_path.exists(),
                    smiley_path.stat().st_size if smiley_path.exists() else 0)
    else:
        logger.warning("[FontPool] smiley_sans 字体未找到 (搜索 SmileySans-Oblique.ttf / 优设标题黑.TTF)")
    smiley_license = _find_font_file("LICENSE.txt")  # 许可证文件
    fonts.append(FontConfig(
        font_id="smiley_sans",
        font_name="得意黑" if smiley_path and "Smiley" in smiley_path.name else "优设标题黑",
        font_family="Smiley Sans" if smiley_path and "Smiley" in smiley_path.name else "YouShe BiaoTiHei",
        font_weight="Oblique",
        font_path=str(smiley_path) if smiley_path else "",
        supports_chinese=True,
        license_name="SIL Open Font License 1.1",
        license_path=str(smiley_license) if smiley_license else "",
        enabled=smiley_enabled,
        fallback_font_id=DEFAULT_FONT_ID,
    ))

    # 按 font_id 排序，保证确定性
    fonts.sort(key=lambda f: f.font_id)
    
    # 启动时打印注册表摘要
    enabled_ids = [f.font_id for f in fonts if f.enabled]
    disabled_ids = [f.font_id for f in fonts if not f.enabled]
    logger.info("[FontPool] 字体注册表: enabled=%s, disabled=%s", enabled_ids, disabled_ids)
    
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
    
    失败时输出绝对路径、exists、文件大小和异常详情。
    """
    font_path = font_config.font_path
    abs_path = os.path.abspath(font_path) if font_path else "<empty>"
    file_exists = os.path.exists(font_path) if font_path else False
    file_size = os.path.getsize(font_path) if file_exists else 0
    
    if not font_path or not file_exists:
        logger.error(
            "[FontPool] 字体验证失败 - id=%s, path=%s, abs_path=%s, exists=%s, size=%d",
            font_config.font_id, font_path, abs_path, file_exists, file_size,
        )
        return FontLoadResult(
            success=False,
            font_config=font_config,
            error=f"字体文件不存在: {abs_path} (exists={file_exists}, size={file_size})",
        )

    if file_size <= 0:
        logger.error(
            "[FontPool] 字体文件为空 - id=%s, path=%s, abs_path=%s, size=%d",
            font_config.font_id, font_path, abs_path, file_size,
        )
        return FontLoadResult(
            success=False,
            font_config=font_config,
            error=f"字体文件为空: {abs_path} (size={file_size})",
        )

    try:
        from PIL import Image, ImageDraw, ImageFont

        font = ImageFont.truetype(_to_pil_font_path(font_path), 38)
        img = Image.new("RGBA", (720, 100), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        bbox = draw.textbbox((0, 0), _FONT_TEST_TEXT, font=font)
        width = bbox[2] - bbox[0]
        height = bbox[3] - bbox[1]

        if width <= 0 or height <= 0:
            logger.error(
                "[FontPool] 字体渲染宽高无效 - id=%s, path=%s, width=%d, height=%d",
                font_config.font_id, abs_path, width, height,
            )
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
        logger.error(
            "[FontPool] 字体加载异常 - id=%s, path=%s, abs_path=%s, exists=%s, size=%d, error=%s",
            font_config.font_id, font_path, abs_path, file_exists, file_size, e,
            exc_info=True,
        )
        return FontLoadResult(
            success=False,
            font_config=font_config,
            error=f"字体加载或渲染失败: {e} (path={abs_path}, exists={file_exists}, size={file_size})",
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
        font = ImageFont.truetype(_to_pil_font_path(font_path), font_size)
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
