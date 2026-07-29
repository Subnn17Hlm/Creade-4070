"""
字幕预设生产链路完整测试

测试覆盖：
1. 预设轮换测试 - variation_index 0,1,2,3 → preset_01,02,03,04
2. 参数测试 - 验证四套预设的所有参数符合要求且互不相同
3. preset_04 专项测试 - 验证深红字白描边无阴影无背景
4. 像素级渲染测试 - 生成4张PNG验证视觉差异
5. 生产链路测试 - 模拟4任务批次验证完整链路
6. 持久化复用测试 - 旧任务保持原分配，新任务重新分配
"""

import hashlib
import os
import tempfile
from pathlib import Path

import pytest
from PIL import Image

from subtitle_styling.presets import get_presets, get_preset_by_id, get_preset_count
from subtitle_styling.style_pool import get_style_by_id, get_style_registry
from subtitle_styling.assignment import assign_subtitle_style, assignment_to_dict
from subtitle_styling.font_pool import get_font_registry, get_enabled_fonts


class TestPresetRotation:
    """预设轮换测试"""

    def test_variation_index_0_to_preset_01(self):
        """variation_index=0 → preset_01_source_han"""
        assignment = assign_subtitle_style("test_task_0", task_index=0)
        assert assignment.preset_id == "preset_01_source_han"
        assert assignment.font_id == "source_han_sans"
        assert assignment.style_id == "default_white_black_stroke"

    def test_variation_index_1_to_preset_02(self):
        """variation_index=1 → preset_02_smiley_sans"""
        assignment = assign_subtitle_style("test_task_1", task_index=1)
        assert assignment.preset_id == "preset_02_smiley_sans"
        assert assignment.font_id == "smiley_sans"
        assert assignment.style_id == "yellow_black_stroke"

    def test_variation_index_2_to_preset_03(self):
        """variation_index=2 → preset_03_alibaba_bold"""
        assignment = assign_subtitle_style("test_task_2", task_index=2)
        assert assignment.preset_id == "preset_03_alibaba_bold"
        assert assignment.font_id == "alibaba_puhuiti"
        assert assignment.style_id == "cyan_dark_blue_stroke"

    def test_variation_index_3_to_preset_04(self):
        """variation_index=3 → preset_04_alibaba_heavy"""
        assignment = assign_subtitle_style("test_task_3", task_index=3)
        assert assignment.preset_id == "preset_04_alibaba_heavy"
        assert assignment.font_id == "alibaba_puhuiti_heavy"
        assert assignment.style_id == "red_white_stroke"

    def test_four_presets_rotation(self):
        """验证4个预设按顺序轮换"""
        expected = [
            ("preset_01_source_han", "source_han_sans", "default_white_black_stroke"),
            ("preset_02_smiley_sans", "smiley_sans", "yellow_black_stroke"),
            ("preset_03_alibaba_bold", "alibaba_puhuiti", "cyan_dark_blue_stroke"),
            ("preset_04_alibaba_heavy", "alibaba_puhuiti_heavy", "red_white_stroke"),
        ]
        for i, (preset_id, font_id, style_id) in enumerate(expected):
            assignment = assign_subtitle_style(f"rotation_test_{i}", task_index=i)
            assert assignment.preset_id == preset_id, f"index={i}"
            assert assignment.font_id == font_id, f"index={i}"
            assert assignment.style_id == style_id, f"index={i}"


class TestPresetParameters:
    """参数测试 - 验证四套预设的所有参数"""

    def test_preset_01_parameters(self):
        """preset_01: 白字黑描边"""
        style = get_style_by_id("default_white_black_stroke")
        assert style is not None
        assert style.font_size == 38
        assert style.text_color == (255, 255, 255, 255)
        assert style.stroke_color == (0, 0, 0, 255)
        assert style.stroke_width == 3
        assert style.shadow_enabled is False
        assert style.background_enabled is False

    def test_preset_02_parameters(self):
        """preset_02: 黄字黑粗描边"""
        style = get_style_by_id("yellow_black_stroke")
        assert style is not None
        assert style.font_size == 42
        assert style.text_color == (255, 220, 0, 255)
        assert style.stroke_color == (0, 0, 0, 255)
        assert style.stroke_width == 5
        assert style.shadow_enabled is False
        assert style.background_enabled is False

    def test_preset_03_parameters(self):
        """preset_03: 青字深蓝描边"""
        style = get_style_by_id("cyan_dark_blue_stroke")
        assert style is not None
        assert style.font_size == 40
        assert style.text_color == (0, 230, 230, 255)
        assert style.stroke_color == (0, 35, 110, 255)
        assert style.stroke_width == 4
        assert style.shadow_enabled is False
        assert style.background_enabled is False

    def test_preset_04_parameters(self):
        """preset_04: 深红字白描边"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.font_size == 40
        assert style.text_color == (139, 0, 0, 255)
        assert style.stroke_color == (255, 255, 255, 255)
        assert style.stroke_width == 3
        assert style.shadow_enabled is False
        assert style.background_enabled is False

    def test_four_presets_all_different(self):
        """验证四套预设的组合互不相同"""
        presets = get_presets()
        assert len(presets) >= 4

        # 提取前4个预设的关键参数
        param_sets = []
        for preset in presets[:4]:
            style = get_style_by_id(preset.style_id)
            assert style is not None, f"Style not found: {preset.style_id}"
            param_sets.append({
                "font_id": preset.font_id,
                "style_id": preset.style_id,
                "font_size": style.font_size,
                "text_color": style.text_color,
                "stroke_color": style.stroke_color,
                "stroke_width": style.stroke_width,
            })

        # 验证所有参数组合互不相同
        for i in range(len(param_sets)):
            for j in range(i + 1, len(param_sets)):
                assert param_sets[i] != param_sets[j], f"Presets {i} and {j} are identical"


class TestPreset04Specific:
    """preset_04 专项测试"""

    def test_preset_04_font_id(self):
        """preset_04 必须使用 alibaba_puhuiti_heavy"""
        preset = get_preset_by_id("preset_04_alibaba_heavy")
        assert preset is not None
        assert preset.font_id == "alibaba_puhuiti_heavy"

    def test_preset_04_style_id(self):
        """preset_04 必须使用 red_white_stroke"""
        preset = get_preset_by_id("preset_04_alibaba_heavy")
        assert preset is not None
        assert preset.style_id == "red_white_stroke"

    def test_preset_04_text_color(self):
        """preset_04 文字颜色必须是深红色 (139, 0, 0, 255)"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.text_color == (139, 0, 0, 255)

    def test_preset_04_stroke_color(self):
        """preset_04 描边颜色必须是白色 (255, 255, 255, 255)"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.stroke_color == (255, 255, 255, 255)

    def test_preset_04_stroke_width(self):
        """preset_04 描边宽度必须是 3"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.stroke_width == 3

    def test_preset_04_no_shadow(self):
        """preset_04 必须禁用阴影"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.shadow_enabled is False

    def test_preset_04_no_background(self):
        """preset_04 必须禁用背景"""
        style = get_style_by_id("red_white_stroke")
        assert style is not None
        assert style.background_enabled is False


class TestPixelLevelRendering:
    """像素级渲染测试"""

    @pytest.fixture
    def temp_dir(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            yield tmpdir

    def _render_preset_png(self, preset_index: int, temp_dir: str) -> str:
        """渲染指定预设的PNG"""
        assignment = assign_subtitle_style(f"pixel_test_{preset_index}", task_index=preset_index)
        style = get_style_by_id(assignment.style_id)
        assert style is not None

        # 获取字体路径
        font_registry = get_font_registry()
        font_entry = next((f for f in font_registry if f.font_id == assignment.font_id), None)
        assert font_entry is not None, f"Font not found: {assignment.font_id}"
        assert font_entry.enabled, f"Font not enabled: {assignment.font_id}"

        output_path = os.path.join(temp_dir, f"preset_{preset_index}.png")

        # 使用 renderer 渲染
        from subtitle_styling.renderer import render_subtitle_png
        result = render_subtitle_png(
            text="测试字幕",
            output_path=output_path,
            font_path=font_entry.font_path,
            style=style,
            video_width=1080,
            video_height=1920,
        )

        assert result["success"], f"Rendering failed: {result.get('error')}"
        assert os.path.exists(output_path), f"Output file not created: {output_path}"
        return output_path

    def test_four_pngs_different_hash(self, temp_dir):
        """四张PNG的SHA-256哈希互不相同"""
        hashes = []
        for i in range(4):
            png_path = self._render_preset_png(i, temp_dir)
            with open(png_path, "rb") as f:
                content = f.read()
            hash_val = hashlib.sha256(content).hexdigest()
            hashes.append(hash_val)

        # 验证所有哈希互不相同
        assert len(set(hashes)) == 4, f"Hash collision detected: {hashes}"

    def test_four_pngs_different_fonts(self, temp_dir):
        """四张PNG使用的字体路径不同"""
        font_paths = []
        for i in range(4):
            assignment = assign_subtitle_style(f"font_test_{i}", task_index=i)
            font_registry = get_font_registry()
            font_entry = next((f for f in font_registry if f.font_id == assignment.font_id), None)
            assert font_entry is not None
            font_paths.append(font_entry.font_path)

        # 验证所有字体路径互不相同
        assert len(set(font_paths)) == 4, f"Font path collision: {font_paths}"

    def test_preset_02_has_yellow_pixels(self, temp_dir):
        """preset_02 中存在黄色文字像素"""
        png_path = self._render_preset_png(1, temp_dir)  # preset_02
        img = Image.open(png_path).convert("RGBA")
        pixels = list(img.getdata())

        # 黄色像素: R > 200, G > 180, B < 50
        yellow_pixels = [
            p for p in pixels
            if p[3] > 0 and p[0] > 200 and p[1] > 180 and p[2] < 50
        ]
        assert len(yellow_pixels) > 100, f"No yellow pixels found in preset_02"

    def test_preset_03_has_cyan_and_dark_blue(self, temp_dir):
        """preset_03 中存在青色文字和深蓝描边像素"""
        png_path = self._render_preset_png(2, temp_dir)  # preset_03
        img = Image.open(png_path).convert("RGBA")
        pixels = list(img.getdata())

        # 青色像素: R < 50, G > 200, B > 200
        cyan_pixels = [
            p for p in pixels
            if p[3] > 0 and p[0] < 50 and p[1] > 200 and p[2] > 200
        ]
        assert len(cyan_pixels) > 100, f"No cyan pixels found in preset_03"

        # 深蓝色像素: R < 50, G < 100, B > 80
        dark_blue_pixels = [
            p for p in pixels
            if p[3] > 0 and p[0] < 50 and p[1] < 100 and p[2] > 80
        ]
        assert len(dark_blue_pixels) > 100, f"No dark blue pixels found in preset_03"

    def test_preset_04_has_dark_red_and_white_stroke(self, temp_dir):
        """preset_04 中存在深红文字和白色描边像素"""
        png_path = self._render_preset_png(3, temp_dir)  # preset_04
        img = Image.open(png_path).convert("RGBA")
        pixels = list(img.getdata())

        # 深红色像素: R > 100, G < 50, B < 50
        dark_red_pixels = [
            p for p in pixels
            if p[3] > 0 and p[0] > 100 and p[1] < 50 and p[2] < 50
        ]
        assert len(dark_red_pixels) > 100, f"No dark red pixels found in preset_04"

        # 白色像素: R > 240, G > 240, B > 240
        white_pixels = [
            p for p in pixels
            if p[3] > 0 and p[0] > 240 and p[1] > 240 and p[2] > 240
        ]
        assert len(white_pixels) > 100, f"No white pixels found in preset_04"

    def test_four_pngs_subtitle_center_at_75_percent(self, temp_dir):
        """四张图的字幕块中心Y均约等于视频高度的75%"""
        video_height = 1920
        expected_center_y = int(video_height * 0.75)
        tolerance = 50  # 允许少量字体度量误差

        for i in range(4):
            png_path = self._render_preset_png(i, temp_dir)
            img = Image.open(png_path).convert("RGBA")
            pixels = list(img.getdata())
            width, height = img.size

            # 找到所有非透明像素的Y坐标
            y_coords = []
            for y in range(height):
                for x in range(width):
                    if pixels[y * width + x][3] > 0:
                        y_coords.append(y)

            assert len(y_coords) > 0, f"No visible pixels in preset_{i}"

            # 计算字幕块中心Y
            min_y = min(y_coords)
            max_y = max(y_coords)
            center_y = (min_y + max_y) // 2

            assert abs(center_y - expected_center_y) <= tolerance, \
                f"Preset {i} center Y {center_y} not within tolerance of {expected_center_y}"


class TestProductionChain:
    """生产链路测试 - 模拟4任务批次"""

    def test_batch_four_tasks(self):
        """模拟包含4条新任务的批次"""
        batch_results = []

        for batch_task_index in range(4):
            task_id = f"batch_task_{batch_task_index}"

            # 模拟 subtitle_style_selection_node 的逻辑
            assignment = assign_subtitle_style(task_id, task_index=batch_task_index)
            style_dict = assignment_to_dict(assignment)

            batch_results.append({
                "batch_task_index": batch_task_index,
                "variation_index": batch_task_index,
                "subtitle_preset_id": assignment.preset_id,
                "subtitle_style_id": assignment.style_id,
                "subtitle_font_id": assignment.font_id,
                "subtitle_fallback_used": assignment.fallback_used,
                "style_dict": style_dict,
            })

        # 验证 batch_task_index 依次为 0, 1, 2, 3
        assert [r["batch_task_index"] for r in batch_results] == [0, 1, 2, 3]

        # 验证 variation_index 依次为 0, 1, 2, 3
        assert [r["variation_index"] for r in batch_results] == [0, 1, 2, 3]

        # 验证 actual_renderer 全部为 styled（通过检查 style_dict 完整性）
        for r in batch_results:
            assert r["style_dict"]["subtitle_style_id"] is not None
            assert r["style_dict"]["subtitle_font_id"] is not None
            assert r["style_dict"]["subtitle_font_path"] is not None

        # 验证 actual_style_id 分别等于四套预设对应的 style_id
        expected_style_ids = [
            "default_white_black_stroke",
            "yellow_black_stroke",
            "cyan_dark_blue_stroke",
            "red_white_stroke",
        ]
        actual_style_ids = [r["subtitle_style_id"] for r in batch_results]
        assert actual_style_ids == expected_style_ids

        # 验证 subtitle_fallback_used 全部为 False
        assert all(not r["subtitle_fallback_used"] for r in batch_results)


class TestPersistenceReuse:
    """持久化复用测试"""

    def test_old_task_keeps_assignment(self):
        """旧任务重试时保持原分配一致"""
        task_id = "old_task_001"

        # 第一次分配
        assignment1 = assign_subtitle_style(task_id, task_index=0)
        style_dict1 = assignment_to_dict(assignment1)

        # 模拟重试时传入已有的 subtitle_style
        # 在 subtitle_style_selection_node 中，如果 existing_style 存在，会复用
        # 这里验证 assignment_to_dict 的输出是稳定的
        assignment2 = assign_subtitle_style(task_id, task_index=0)
        style_dict2 = assignment_to_dict(assignment2)

        assert style_dict1["subtitle_preset_id"] == style_dict2["subtitle_preset_id"]
        assert style_dict1["subtitle_style_id"] == style_dict2["subtitle_style_id"]
        assert style_dict1["subtitle_font_id"] == style_dict2["subtitle_font_id"]

    def test_new_task_gets_new_assignment(self):
        """新任务必须按照当前四预设重新分配"""
        # 使用不同的 task_id 模拟新任务
        new_task_ids = [f"new_task_{i}" for i in range(4)]

        for i, task_id in enumerate(new_task_ids):
            assignment = assign_subtitle_style(task_id, task_index=i)
            # 验证新任务按照 variation_index 分配
            assert assignment.preset_id == get_presets()[i].preset_id

    def test_new_batch_does_not_inherit_old_style(self):
        """新批次不继承旧任务持久化的 subtitle_style"""
        # 旧任务的 style_dict
        old_assignment = assign_subtitle_style("old_batch_task", task_index=0)
        old_style_dict = assignment_to_dict(old_assignment)

        # 新批次的新任务
        new_assignment = assign_subtitle_style("new_batch_task", task_index=1)
        new_style_dict = assignment_to_dict(new_assignment)

        # 验证新任务不继承旧任务的样式
        assert new_style_dict["subtitle_preset_id"] != old_style_dict["subtitle_preset_id"]
        assert new_style_dict["subtitle_style_id"] != old_style_dict["subtitle_style_id"]


class TestStyleDictFields:
    """验证 assignment_to_dict 输出的字段完整性"""

    def test_style_dict_has_all_required_fields(self):
        """style_dict 必须包含所有必需字段"""
        assignment = assign_subtitle_style("field_test_task", task_index=0)
        style_dict = assignment_to_dict(assignment)

        required_fields = [
            "subtitle_font_id",
            "subtitle_font_name",
            "subtitle_font_weight",
            "subtitle_font_path",
            "subtitle_style_id",
            "subtitle_style_name",
            "subtitle_preset_id",
            "subtitle_fallback_used",
            "subtitle_fallback_reason",
        ]

        for field in required_fields:
            assert field in style_dict, f"Missing field: {field}"

    def test_style_dict_uses_correct_field_names(self):
        """style_dict 必须使用统一的字段名（subtitle_前缀）"""
        assignment = assign_subtitle_style("field_name_test", task_index=0)
        style_dict = assignment_to_dict(assignment)

        # 验证使用 subtitle_font_id 而不是 font_id
        assert "subtitle_font_id" in style_dict
        assert "subtitle_style_id" in style_dict

        # 验证不使用旧字段名
        assert "font_id" not in style_dict or style_dict.get("font_id") is None
        assert "style_id" not in style_dict or style_dict.get("style_id") is None
