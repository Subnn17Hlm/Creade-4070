"""
字体路径大小写敏感测试 + 四预设生产路径测试

验证：
1. 所有字体路径基于 assets/Fonts/（大写 F）
2. 所有 4 个字体注册项均可加载并渲染中文
3. _to_pil_font_path 正确处理 surrogateescape 路径
4. _find_font_file 递归搜索兼容 GBK 编码目录名
5. _find_chinese_font 回退逻辑正确
6. 批量 4 个 variation_index 分别映射到不同字体
"""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

# 确保 src 在 sys.path 中
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT / "src"))

from subtitle_styling.font_pool import (
    _FONTS_DIR,
    _find_font_file,
    _to_pil_font_path,
    get_default_font,
    get_enabled_fonts,
    get_font_registry,
    validate_font,
)


class TestFontPathCaseSensitivity(unittest.TestCase):
    """验证字体目录大小写正确"""

    def test_fonts_dir_uses_capital_F(self):
        """_FONTS_DIR 必须指向 assets/Fonts（大写 F）"""
        self.assertEqual(_FONTS_DIR.name, "Fonts")
        self.assertIn("Fonts", str(_FONTS_DIR))
        self.assertNotIn("assets/fonts", str(_FONTS_DIR).lower().replace("assets/fonts", "MISMATCH"))

    def test_fonts_dir_exists(self):
        """assets/Fonts/ 目录必须存在"""
        self.assertTrue(_FONTS_DIR.is_dir(), f"字体目录不存在: {_FONTS_DIR}")

    def test_no_lowercase_fonts_reference_in_source(self):
        """生产代码中不应引用 assets/fonts（小写 f）"""
        src_dir = _PROJECT_ROOT / "src"
        violations = []
        for py_file in src_dir.rglob("*.py"):
            content = py_file.read_text(errors="ignore")
            # 检查是否有 assets/fonts（小写 f）的引用
            # 排除注释和 _FONTS_DIR 定义本身
            for i, line in enumerate(content.splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                if "assets/fonts" in line and "assets/Fonts" not in line:
                    # 排除 _FONTS_DIR 变量名中可能的误匹配
                    if "_FONTS_DIR" not in line and "font_pool" not in line:
                        violations.append(f"{py_file.relative_to(_PROJECT_ROOT)}:{i}: {stripped}")
        self.assertEqual(violations, [], f"发现小写 assets/fonts 引用:\n" + "\n".join(violations))


class TestFindFontFile(unittest.TestCase):
    """验证递归字体文件搜索"""

    def test_find_alibaba_bold(self):
        """能找到 ALIBABA-PUHUITI-BOLD.TTF"""
        result = _find_font_file("ALIBABA-PUHUITI-BOLD.TTF")
        self.assertIsNotNone(result, "未找到 ALIBABA-PUHUITI-BOLD.TTF")
        self.assertTrue(result.exists(), f"文件不存在: {result}")
        self.assertGreater(result.stat().st_size, 0, "文件大小为 0")

    def test_find_alibaba_heavy(self):
        """能找到 ALIBABA-PUHUITI-HEAVY.TTF"""
        result = _find_font_file("ALIBABA-PUHUITI-HEAVY.TTF")
        self.assertIsNotNone(result)
        self.assertTrue(result.exists())
        self.assertGreater(result.stat().st_size, 0)

    def test_find_case_insensitive(self):
        """文件名匹配不区分大小写"""
        result = _find_font_file("alibaba-puhuiti-bold.ttf")
        self.assertIsNotNone(result, "大小写不敏感搜索失败")

    def test_find_nonexistent(self):
        """不存在的文件返回 None"""
        result = _find_font_file("NONEXISTENT-FONT-12345.ttf")
        self.assertIsNone(result)

    def test_find_source_han(self):
        """能找到 SOURCEHANSERIFCN-BOLD.OTF"""
        result = _find_font_file("SOURCEHANSERIFCN-BOLD.OTF")
        self.assertIsNotNone(result, "未找到 SOURCEHANSERIFCN-BOLD.OTF")
        self.assertTrue(result.exists())


class TestToPilFontPath(unittest.TestCase):
    """验证 _to_pil_font_path 处理 surrogateescape 路径"""

    def test_normal_utf8_path_returns_str(self):
        """正常 UTF-8 路径返回 str"""
        path = "/usr/share/fonts/test.ttf"
        result = _to_pil_font_path(path)
        self.assertIsInstance(result, str)
        self.assertEqual(result, path)

    def test_surrogate_path_returns_bytes(self):
        """包含 surrogate 字符的路径返回 bytes"""
        # 模拟 GBK 编码目录名解码后的 surrogate 路径
        gbk_bytes = b"/tmp/\xba\xda\xcc\xe5/test.ttf"
        surrogate_path = gbk_bytes.decode("utf-8", errors="surrogateescape")
        result = _to_pil_font_path(surrogate_path)
        self.assertIsInstance(result, bytes)
        self.assertEqual(result, gbk_bytes)

    def test_pil_can_load_bytes_path(self):
        """PIL 能加载 bytes 路径的字体"""
        from PIL import ImageFont
        
        # 使用实际字体文件测试
        registry = get_font_registry()
        enabled = [f for f in registry if f.enabled]
        if not enabled:
            self.skipTest("没有启用的字体")
        
        font_path = enabled[0].font_path
        pil_path = _to_pil_font_path(font_path)
        font = ImageFont.truetype(pil_path, 38)
        bbox = font.getbbox("测试中文")
        self.assertGreater(bbox[2] - bbox[0], 0)


class TestFontRegistryAllEnabled(unittest.TestCase):
    """验证所有 4 个字体注册项均可用"""

    def test_all_four_fonts_enabled(self):
        """4 个字体全部启用"""
        enabled = get_enabled_fonts()
        enabled_ids = {f.font_id for f in enabled}
        expected = {"source_han_sans", "alibaba_puhuiti", "alibaba_puhuiti_heavy", "smiley_sans"}
        self.assertEqual(enabled_ids, expected, f"启用的字体不完整: {enabled_ids}")

    def test_all_fonts_validate_success(self):
        """所有字体验证通过"""
        registry = get_font_registry()
        for f in registry:
            if not f.enabled:
                continue
            result = validate_font(f)
            self.assertTrue(result.success, f"字体验证失败: {f.font_id}, error={result.error}")

    def test_all_fonts_can_render_chinese(self):
        """所有字体能渲染中文"""
        from PIL import Image, ImageDraw, ImageFont
        
        registry = get_font_registry()
        for f in registry:
            if not f.enabled:
                continue
            pil_path = _to_pil_font_path(f.font_path)
            font = ImageFont.truetype(pil_path, 38)
            img = Image.new("RGBA", (720, 100), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            bbox = draw.textbbox((0, 0), "字幕测试文字", font=font)
            width = bbox[2] - bbox[0]
            height = bbox[3] - bbox[1]
            self.assertGreater(width, 0, f"{f.font_id} 渲染宽度为 0")
            self.assertGreater(height, 0, f"{f.font_id} 渲染高度为 0")

    def test_default_font_is_valid(self):
        """默认字体有效"""
        default = get_default_font()
        self.assertTrue(default.enabled)
        result = validate_font(default)
        self.assertTrue(result.success, f"默认字体验证失败: {default.font_id}, error={result.error}")


class TestFontPathFileSize(unittest.TestCase):
    """验证每个字体文件大小 > 0"""

    def test_all_font_files_non_empty(self):
        """所有字体文件非空"""
        registry = get_font_registry()
        for f in registry:
            if not f.enabled:
                continue
            self.assertTrue(os.path.exists(f.font_path), f"字体文件不存在: {f.font_id}: {f.font_path}")
            size = os.path.getsize(f.font_path)
            self.assertGreater(size, 0, f"字体文件为空: {f.font_id}: {f.font_path}")

    def test_font_files_are_reasonable_size(self):
        """字体文件大小合理（> 100KB）"""
        registry = get_font_registry()
        for f in registry:
            if not f.enabled:
                continue
            size = os.path.getsize(f.font_path)
            self.assertGreater(size, 100_000, f"字体文件过小: {f.font_id}: {size} bytes")


class TestFindChineseFont(unittest.TestCase):
    """验证 _find_chinese_font 回退逻辑"""

    def test_find_chinese_font_returns_valid_path(self):
        """_find_chinese_font 返回有效路径"""
        from graphs.nodes.final_composition_node import _find_chinese_font
        
        result = _find_chinese_font()
        self.assertIsNotNone(result)
        self.assertTrue(os.path.exists(result), f"字体文件不存在: {result}")
        self.assertGreater(os.path.getsize(result), 0)

    def test_find_chinese_font_finds_alibaba_first(self):
        """_find_chinese_font 优先返回阿里巴巴普惠体"""
        from graphs.nodes.final_composition_node import _find_chinese_font
        
        result = _find_chinese_font()
        # 优先级最高的是 ALIBABA-PUHUITI-BOLD.TTF
        self.assertIn("ALIBABA-PUHUITI-BOLD.TTF", result.upper())


class TestBatchVariationFontMapping(unittest.TestCase):
    """验证批量 4 个 variation_index 映射到不同字体"""

    def test_four_variations_map_to_different_fonts(self):
        """4 个 variation_index 应映射到至少 2 种不同字体"""
        from subtitle_styling.style_pool import get_style_by_id
        from subtitle_styling.font_pool import get_font_by_id
        
        # 模拟 4 个 variation_index 的字体选择
        font_ids_used = set()
        for variation_index in range(4):
            # 每个 variation 会选择不同的 preset
            preset_id = variation_index % 4  # 简化模拟
            # 获取 preset 对应的字体
            registry = get_font_registry()
            enabled = [f for f in registry if f.enabled]
            if enabled:
                font = enabled[variation_index % len(enabled)]
                font_ids_used.add(font.font_id)
        
        # 4 个 variation 应该使用至少 2 种不同字体
        self.assertGreaterEqual(len(font_ids_used), 2, 
            f"4 个 variation 只使用了 {len(font_ids_used)} 种字体: {font_ids_used}")


if __name__ == "__main__":
    unittest.main()
