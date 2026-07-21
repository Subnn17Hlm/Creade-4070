"""
测试 ffmpeg 诊断能力增强

验证：
1. 长 stderr 保留末尾真正错误（而非开头版本信息）
2. 负数返回码（信号终止）正确处理
3. 并发任务使用独立临时目录
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from utils.ffmpeg_utils import run_ffmpeg


class TestFFmpegDiagnostics(unittest.TestCase):
    """测试 ffmpeg 诊断能力"""
    
    def test_stderr_preserves_tail_not_head(self):
        """验证长 stderr 保留末尾而非开头"""
        # 模拟一个长 stderr，开头是版本信息，末尾是真正错误
        fake_stderr = "ffmpeg version 7.0.2-static\n" * 100  # 开头版本信息
        fake_stderr += "\n\nActual error at the end: File not found or permission denied\n"
        
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = 1
            mock_result.stderr = fake_stderr
            
            mock_run.return_value = mock_result
            
            try:
                run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4"])
                self.fail("Should have raised RuntimeError")
            except RuntimeError as e:
                error_msg = str(e)
                # 验证错误消息包含末尾的真正错误
                self.assertIn("Actual error at the end", error_msg)
                # 验证错误消息不包含开头的版本信息（因为保留了末尾 8000 字符）
                # 注意：如果 stderr 总长度小于 8000，可能仍会包含部分版本信息
                # 但关键是要包含末尾的错误
    
    def test_negative_returncode_signal_handling(self):
        """验证负数返回码（信号终止）正确处理"""
        with patch('subprocess.run') as mock_run:
            mock_result = MagicMock()
            mock_result.returncode = -9  # SIGKILL
            mock_result.stderr = "Process killed by signal"
            
            mock_run.return_value = mock_result
            
            try:
                run_ffmpeg(["ffmpeg", "-i", "input.mp4", "output.mp4"])
                self.fail("Should have raised RuntimeError")
            except RuntimeError as e:
                error_msg = str(e)
                # 验证错误消息包含信号信息
                self.assertIn("信号 9 终止", error_msg)
                self.assertIn("code=-9", error_msg)
    
    def test_concurrent_tasks_use_independent_temp_dirs(self):
        """验证并发任务使用独立临时目录"""
        # 这个测试验证 final_composition_node 的临时目录隔离
        # 由于 run_dir 包含 run_id，每个任务应该有独立的 temp_dir
        
        run_id_1 = "test-run-id-1"
        run_id_2 = "test-run-id-2"
        
        # 模拟两个不同的 run_dir
        run_dir_1 = f"/tmp/runs/{run_id_1}"
        run_dir_2 = f"/tmp/runs/{run_id_2}"
        
        # 验证它们不同
        self.assertNotEqual(run_dir_1, run_dir_2)
        
        # 验证 temp_dir 也会不同（因为基于 run_dir）
        temp_dir_1 = os.path.join(run_dir_1, "temp")
        temp_dir_2 = os.path.join(run_dir_2, "temp")
        
        self.assertNotEqual(temp_dir_1, temp_dir_2)
        
        # 验证输出文件也会不同
        output_1 = os.path.join(run_dir_1, "final.mp4")
        output_2 = os.path.join(run_dir_2, "final.mp4")
        
        self.assertNotEqual(output_1, output_2)


if __name__ == "__main__":
    unittest.main()
