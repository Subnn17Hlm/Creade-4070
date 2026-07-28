"""
字幕预设系统测试

验证：
1. 4 个预设定义正确
2. 均衡轮换算法（4预设6任务 → 0,1,2,3,0,1）
3. 确定性（同一 task_id 总是获得相同预设）
4. 渲染管线使用选择结果
"""
import sys
import os

# 添加 src 目录到路径
src_path = os.path.join(os.path.dirname(__file__), 'src')
if src_path not in sys.path:
    sys.path.insert(0, src_path)

# 设置工作目录
os.chdir(os.path.dirname(__file__))

from src.subtitle_styling.presets import (
    get_presets,
    get_preset_by_id,
    get_preset_for_task,
    get_preset_for_task_id,
    get_preset_count,
    validate_preset,
)
from src.subtitle_styling.assignment import (
    assign_subtitle_style,
    assignment_to_dict,
)


def test_preset_definitions():
    """测试 1: 4 个预设定义正确"""
    print("=" * 60)
    print("测试 1: 预设定义")
    print("=" * 60)
    
    presets = get_presets()
    assert len(presets) == 4, f"期望 4 个预设，实际 {len(presets)}"
    
    for i, preset in enumerate(presets):
        valid, error = validate_preset(preset)
        print(f"预设 {i+1}: {preset.preset_id}")
        print(f"  名称: {preset.preset_name}")
        print(f"  字体: {preset.font_id}")
        print(f"  样式: {preset.style_id}")
        print(f"  有效: {valid}")
        if not valid:
            print(f"  错误: {error}")
        assert valid, f"预设 {preset.preset_id} 验证失败: {error}"
    
    print("✓ 所有预设定义有效\n")


def test_balanced_rotation():
    """测试 2: 均衡轮换算法（4预设6任务 → 0,1,2,3,0,1）"""
    print("=" * 60)
    print("测试 2: 均衡轮换")
    print("=" * 60)
    
    preset_count = get_preset_count()
    print(f"预设数量: {preset_count}")
    
    # 测试 6 个任务的分配
    expected_indices = [0, 1, 2, 3, 0, 1]
    actual_presets = []
    
    for task_index in range(6):
        preset = get_preset_for_task(task_index)
        actual_presets.append(preset.preset_id)
        expected_preset = get_presets()[expected_indices[task_index]]
        
        print(f"任务 {task_index}: {preset.preset_id} (期望: {expected_preset.preset_id})")
        assert preset.preset_id == expected_preset.preset_id, \
            f"任务 {task_index} 分配错误: 期望 {expected_preset.preset_id}，实际 {preset.preset_id}"
    
    print("✓ 均衡轮换正确\n")


def test_deterministic_assignment():
    """测试 3: 确定性（同一 task_id 总是获得相同预设）"""
    print("=" * 60)
    print("测试 3: 确定性")
    print("=" * 60)
    
    task_ids = [
        "task-001",
        "task-002",
        "task-003",
        "batch_123_task_456",
        "test_task_abc",
    ]
    
    for task_id in task_ids:
        # 第一次分配
        preset1 = get_preset_for_task_id(task_id)
        # 第二次分配
        preset2 = get_preset_for_task_id(task_id)
        # 第三次分配
        preset3 = get_preset_for_task_id(task_id)
        
        print(f"task_id: {task_id}")
        print(f"  第1次: {preset1.preset_id}")
        print(f"  第2次: {preset2.preset_id}")
        print(f"  第3次: {preset3.preset_id}")
        
        assert preset1.preset_id == preset2.preset_id == preset3.preset_id, \
            f"task_id {task_id} 分配不一致"
    
    print("✓ 确定性验证通过\n")


def test_assignment_with_task_index():
    """测试 4: 使用 task_index 的分配"""
    print("=" * 60)
    print("测试 4: 使用 task_index 的分配")
    print("=" * 60)
    
    # 测试 6 个任务的分配
    for task_index in range(6):
        task_id = f"test-task-{task_index:03d}"
        assignment = assign_subtitle_style(task_id, task_index=task_index)
        
        expected_preset = get_preset_for_task(task_index)
        
        print(f"任务 {task_index} (task_id={task_id}):")
        print(f"  预设: {assignment.preset_id}")
        print(f"  字体: {assignment.font_id}")
        print(f"  样式: {assignment.style_id}")
        
        assert assignment.preset_id == expected_preset.preset_id, \
            f"任务 {task_index} 分配错误: 期望 {expected_preset.preset_id}，实际 {assignment.preset_id}"
    
    print("✓ task_index 分配正确\n")


def test_assignment_serialization():
    """测试 5: 分配结果序列化"""
    print("=" * 60)
    print("测试 5: 分配结果序列化")
    print("=" * 60)
    
    task_id = "test-task-001"
    task_index = 0
    
    assignment = assign_subtitle_style(task_id, task_index=task_index)
    style_dict = assignment_to_dict(assignment)
    
    print(f"分配结果:")
    print(f"  preset_id: {assignment.preset_id}")
    print(f"  font_id: {assignment.font_id}")
    print(f"  style_id: {assignment.style_id}")
    
    print(f"\n序列化结果:")
    for key, value in style_dict.items():
        print(f"  {key}: {value}")
    
    assert "subtitle_preset_id" in style_dict
    assert "subtitle_font_id" in style_dict
    assert "subtitle_style_id" in style_dict
    assert style_dict["subtitle_preset_id"] == assignment.preset_id
    
    print("✓ 序列化正确\n")


def test_different_fonts_and_styles():
    """测试 6: 4 个预设使用不同字体和样式"""
    print("=" * 60)
    print("测试 6: 字体和样式差异")
    print("=" * 60)
    
    presets = get_presets()
    
    font_ids = set()
    style_ids = set()
    
    for preset in presets:
        font_ids.add(preset.font_id)
        style_ids.add(preset.style_id)
        print(f"预设 {preset.preset_id}:")
        print(f"  字体: {preset.font_id}")
        print(f"  样式: {preset.style_id}")
    
    print(f"\n唯一字体数: {len(font_ids)}")
    print(f"唯一样式数: {len(style_ids)}")
    
    assert len(font_ids) == 4, f"期望 4 种不同字体，实际 {len(font_ids)}"
    assert len(style_ids) == 4, f"期望 4 种不同样式，实际 {len(style_ids)}"
    
    print("✓ 字体和样式差异验证通过\n")


def main():
    """运行所有测试"""
    print("\n" + "=" * 60)
    print("字幕预设系统测试")
    print("=" * 60 + "\n")
    
    try:
        test_preset_definitions()
        test_balanced_rotation()
        test_deterministic_assignment()
        test_assignment_with_task_index()
        test_assignment_serialization()
        test_different_fonts_and_styles()
        
        print("=" * 60)
        print("所有测试通过！")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"\n测试失败: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())
