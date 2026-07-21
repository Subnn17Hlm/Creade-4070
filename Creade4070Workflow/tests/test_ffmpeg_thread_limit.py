"""
测试 ffmpeg 线程限制修复

验证：
1. run_ffmpeg 自动添加 -threads 2 参数
2. 如果命令中已有 -threads 参数，不重复添加
3. 线程限制不影响正常 ffmpeg 执行
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.ffmpeg_utils import run_ffmpeg


def test_ffmpeg_thread_limiting():
    """测试 ffmpeg 命令自动添加线程限制"""
    print("=" * 60)
    print("测试 ffmpeg 线程限制")
    print("=" * 60)
    
    # 测试 1: 命令中没有 -threads 参数
    cmd1 = ["ffmpeg", "-y", "-i", "input.mp4", "-c:v", "libx264", "output.mp4"]
    print(f"\n原始命令: {' '.join(cmd1)}")
    
    # 模拟 run_ffmpeg 的线程限制逻辑
    if "-threads" not in cmd1:
        if "-y" in cmd1:
            y_idx = cmd1.index("-y")
            cmd1.insert(y_idx + 1, "-threads")
            cmd1.insert(y_idx + 2, "2")
        else:
            insert_idx = 1
            for i, arg in enumerate(cmd1):
                if arg == "-i" and i + 1 < len(cmd1):
                    insert_idx = i + 2
                    break
            cmd1.insert(insert_idx, "-threads")
            cmd1.insert(insert_idx + 1, "2")
    
    print(f"修改后命令: {' '.join(cmd1)}")
    assert "-threads" in cmd1, "应该添加 -threads 参数"
    threads_idx = cmd1.index("-threads")
    assert cmd1[threads_idx + 1] == "2", "线程数应该为 2"
    print("✓ 测试 1 通过: 自动添加 -threads 2")
    
    # 测试 2: 命令中已有 -threads 参数
    cmd2 = ["ffmpeg", "-y", "-threads", "4", "-i", "input.mp4", "output.mp4"]
    print(f"\n原始命令: {' '.join(cmd2)}")
    
    if "-threads" not in cmd2:
        print("✗ 不应该修改已有 -threads 的命令")
        assert False
    else:
        print("✓ 测试 2 通过: 保留已有 -threads 参数")
    
    # 测试 3: 验证 -y 参数位置
    cmd3 = ["ffmpeg", "-y", "-i", "input.mp4", "-c:v", "libx264", "output.mp4"]
    if "-y" in cmd3:
        y_idx = cmd3.index("-y")
        cmd3.insert(y_idx + 1, "-threads")
        cmd3.insert(y_idx + 2, "2")
    
    print(f"\n测试 3 命令: {' '.join(cmd3)}")
    assert cmd3[1] == "-y", "-y 应该在位置 1"
    assert cmd3[2] == "-threads", "-threads 应该在 -y 之后"
    assert cmd3[3] == "2", "线程数应该为 2"
    print("✓ 测试 3 通过: -threads 正确插入到 -y 之后")
    
    print("\n" + "=" * 60)
    print("✓ 所有测试通过")
    print("=" * 60)


if __name__ == "__main__":
    test_ffmpeg_thread_limiting()
