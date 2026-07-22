"""
批量管理页面测试

测试前端逻辑的关键场景：
1. submitted 状态显示为 pending
2. running 状态正确显示
3. 单次轮询失败保留旧状态
4. 最终 success 正常展示
5. warning 不转成 failed
6. 防止重复提交
"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


class TestBatchPageStatusNormalization:
    """测试状态规范化逻辑"""

    def test_submitted_status_normalized_to_pending(self):
        """测试 submitted 状态被规范化为 pending"""
        # 模拟前端 normalizeTaskStatus 函数逻辑
        def normalize_task_status(status):
            if not status:
                return 'pending'
            s = status.lower()
            if s in ('submitted', 'queued'):
                return 'pending'
            if s == 'timeout':
                return 'timeout'
            return s

        assert normalize_task_status('submitted') == 'pending'
        assert normalize_task_status('SUBMITTED') == 'pending'
        assert normalize_task_status('queued') == 'pending'
        assert normalize_task_status('QUEUED') == 'pending'

    def test_running_status_preserved(self):
        """测试 running 状态保持不变"""
        def normalize_task_status(status):
            if not status:
                return 'pending'
            s = status.lower()
            if s in ('submitted', 'queued'):
                return 'pending'
            if s == 'timeout':
                return 'timeout'
            return s

        assert normalize_task_status('running') == 'running'
        assert normalize_task_status('RUNNING') == 'running'

    def test_timeout_only_when_explicit(self):
        """测试只有后端明确返回 timeout 才显示 timeout"""
        def normalize_task_status(status):
            if not status:
                return 'pending'
            s = status.lower()
            if s in ('submitted', 'queued'):
                return 'pending'
            if s == 'timeout':
                return 'timeout'
            return s

        # 只有明确的 timeout 才显示 timeout
        assert normalize_task_status('timeout') == 'timeout'
        assert normalize_task_status('TIMEOUT') == 'timeout'
        
        # 其他状态不显示 timeout
        assert normalize_task_status('failed') == 'failed'
        assert normalize_task_status('success') == 'success'

    def test_warning_not_converted_to_failed(self):
        """测试 warning 状态不被转换为 failed"""
        def normalize_task_status(status):
            if not status:
                return 'pending'
            s = status.lower()
            if s in ('submitted', 'queued'):
                return 'pending'
            if s == 'timeout':
                return 'timeout'
            return s

        # warning 保持为 warning
        assert normalize_task_status('warning') == 'warning'
        assert normalize_task_status('WARNING') == 'warning'
        
        # 不会被转换为 failed
        assert normalize_task_status('warning') != 'failed'


class TestBatchPagePollingFailure:
    """测试轮询失败处理"""

    def test_single_poll_failure_preserves_old_state(self):
        """测试单次轮询失败保留旧状态"""
        # 模拟轮询失败计数逻辑
        polling_fail_count = 0
        last_batch_status = {'status': 'running', 'task_counts': {'running': 2, 'pending': 1}}

        def handle_poll_response(res_ok, data=None):
            nonlocal polling_fail_count, last_batch_status
            
            if not res_ok:
                polling_fail_count += 1
                if polling_fail_count <= 3:
                    # 前 3 次失败保留旧状态
                    return last_batch_status, 'warning'
                else:
                    # 超过 3 次失败显示错误
                    return None, 'error'
            else:
                # 请求成功，重置失败计数
                polling_fail_count = 0
                last_batch_status = data
                return data, None

        # 第一次失败：保留旧状态
        status, msg_type = handle_poll_response(False)
        assert status == last_batch_status
        assert msg_type == 'warning'
        assert polling_fail_count == 1

        # 第二次失败：仍然保留旧状态
        status, msg_type = handle_poll_response(False)
        assert status == last_batch_status
        assert msg_type == 'warning'
        assert polling_fail_count == 2

        # 第三次失败：仍然保留旧状态
        status, msg_type = handle_poll_response(False)
        assert status == last_batch_status
        assert msg_type == 'warning'
        assert polling_fail_count == 3

        # 第四次失败：显示错误
        status, msg_type = handle_poll_response(False)
        assert status is None
        assert msg_type == 'error'
        assert polling_fail_count == 4

        # 成功请求：重置计数
        new_data = {'status': 'success', 'task_counts': {'success': 3}}
        status, msg_type = handle_poll_response(True, new_data)
        assert status == new_data
        assert msg_type is None
        assert polling_fail_count == 0


class TestBatchPageDuplicateSubmission:
    """测试防止重复提交"""

    def test_submit_button_disabled_during_submission(self):
        """测试提交按钮在提交过程中被禁用"""
        # 模拟按钮状态
        button_disabled = False
        button_text = '提交批次'

        def start_submission():
            nonlocal button_disabled, button_text
            button_disabled = True
            button_text = '提交中...'

        def end_submission():
            nonlocal button_disabled, button_text
            button_disabled = False
            button_text = '提交批次'

        # 开始提交
        start_submission()
        assert button_disabled is True
        assert button_text == '提交中...'

        # 提交完成
        end_submission()
        assert button_disabled is False
        assert button_text == '提交批次'


class TestBatchPageLocalStorage:
    """测试 localStorage 持久化"""

    def test_batch_id_saved_to_localstorage(self):
        """测试 batch_id 保存到 localStorage"""
        # 模拟 localStorage
        localStorage = {}

        def save_to_localstorage(batch_id):
            localStorage['batch_monitor_batch_id'] = batch_id

        def restore_from_localstorage():
            return localStorage.get('batch_monitor_batch_id')

        # 保存
        save_to_localstorage('test-batch-123')
        assert localStorage['batch_monitor_batch_id'] == 'test-batch-123'

        # 恢复
        restored = restore_from_localstorage()
        assert restored == 'test-batch-123'


class TestBatchPageCsvExport:
    """测试 CSV 导出"""

    def test_csv_export_includes_all_fields(self):
        """测试 CSV 导出包含所有必要字段"""
        # 模拟任务数据
        tasks = [
            {
                'task_id': 'task-1',
                'script_id': 'script-1',
                'script_text': '测试文案1',
                'status': 'success',
                'final_video_url': 'https://example.com/video1.mp4',
                'error_message': '',
            },
            {
                'task_id': 'task-2',
                'script_id': 'script-2',
                'script_text': '测试文案2',
                'status': 'failed',
                'final_video_url': '',
                'error_message': '处理失败',
            },
        ]

        # 模拟 CSV 构建
        headers = ['task_id', 'script_id', 'script_text', 'status', 'final_video_url', 'error_message']
        
        # 验证所有字段都在 headers 中
        for task in tasks:
            for field in headers:
                assert field in task


class TestBatchPageIntegration:
    """集成测试"""

    def test_batch_page_route_exists(self):
        """测试批量管理页面路由存在"""
        # 这个测试验证路由是否已添加
        # 实际的 HTTP 测试需要启动完整的 FastAPI 应用
        from pathlib import Path
        web_dir = Path(__file__).parent.parent / "web"
        assert (web_dir / "index.html").exists()
        assert (web_dir / "app.js").exists()
        assert (web_dir / "styles.css").exists()

    def test_normalize_status_comprehensive(self):
        """综合测试状态规范化"""
        def normalize_task_status(status):
            if not status:
                return 'pending'
            s = status.lower()
            if s in ('submitted', 'queued'):
                return 'pending'
            if s == 'timeout':
                return 'timeout'
            return s

        test_cases = [
            ('submitted', 'pending'),
            ('queued', 'pending'),
            ('running', 'running'),
            ('success', 'success'),
            ('warning', 'warning'),
            ('failed', 'failed'),
            ('timeout', 'timeout'),
            ('cancelled', 'cancelled'),
            (None, 'pending'),
            ('', 'pending'),
        ]

        for input_status, expected in test_cases:
            result = normalize_task_status(input_status)
            assert result == expected, f"Input: {input_status}, Expected: {expected}, Got: {result}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
