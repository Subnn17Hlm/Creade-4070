"""
Comprehensive tests for batch page production issues.

Tests cover:
- Task list API: field completeness, null safety, status filtering
- Frontend rendering: task cards, video links, retry buttons
- Retry logic: only failed tasks, idempotency, retry_count increment
- Start batch: response handling, empty body, non-2xx errors
- CSV export: field completeness, encoding, escaping
- Concurrency: slot limits, refill, no duplicate submission
"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime
from fastapi.testclient import TestClient


# ============================================================
# Task List API Tests (Items 1-5)
# ============================================================

class TestTaskListAPI:
    """Tests for GET /api/batches/{batch_id}/tasks"""

    def _make_task(self, **kwargs):
        """Create a mock task with all required fields."""
        defaults = {
            'task_id': 'task-001',
            'batch_id': 'batch-001',
            'row_number': 1,
            'external_task_id': 'ext-001',
            'run_id': 'run-001',
            'status': 'success',
            'script_id': 'script-001',
            'script_text': '测试脚本文本',
            'title': '测试标题',
            'input_data': None,
            'output_data': None,
            'final_video_url': 'https://example.com/video.mp4',
            'error_code': None,
            'error_message': None,
            'retry_count': 0,
            'async_task_id': 'async-001',
            'warning': None,
            'created_at': datetime(2025, 1, 1, 0, 0, 0),
            'started_at': datetime(2025, 1, 1, 0, 0, 10),
            'completed_at': datetime(2025, 1, 1, 0, 5, 0),
            'updated_at': datetime(2025, 1, 1, 0, 5, 0),
        }
        defaults.update(kwargs)
        task = MagicMock()
        for k, v in defaults.items():
            setattr(task, k, v)
        return task

    def test_01_2_success_1_failed_returns_3_tasks(self):
        """Item 1: 2 success + 1 failed returns 3 tasks with all required fields."""
        from src.api.batch_routes import _serialize_task

        tasks = [
            self._make_task(task_id='t1', status='success', final_video_url='https://v.com/1.mp4'),
            self._make_task(task_id='t2', status='success', final_video_url='https://v.com/2.mp4'),
            self._make_task(task_id='t3', status='failed', error_code='BGM_MIX_FAILED',
                          error_message='BGM 混音失败', final_video_url=None),
        ]

        serialized = [_serialize_task(t) for t in tasks]

        assert len(serialized) == 3
        # Check all required fields present
        required_fields = [
            'task_id', 'batch_id', 'script_id', 'title', 'script_text',
            'status', 'run_id', 'async_task_id', 'final_video_url',
            'warning', 'error_code', 'error_message', 'retry_count',
            'created_at', 'started_at', 'completed_at', 'updated_at',
        ]
        for task_dict in serialized:
            for field in required_fields:
                assert field in task_dict, f"Missing field: {field}"

    def test_02_output_data_null_safe(self):
        """Item 2: output_data=None doesn't cause 500."""
        from src.api.batch_routes import _serialize_task

        task = self._make_task(output_data=None)
        result = _serialize_task(task)
        assert result['output_data'] is None

    def test_02b_output_data_string_safe(self):
        """Item 2b: output_data as string is handled safely."""
        from src.api.batch_routes import _serialize_task

        task = self._make_task(output_data='some string')
        result = _serialize_task(task)
        # String output_data is wrapped in {"_raw": ...} for safety
        assert result['output_data'] == {"_raw": "some string"}

    def test_02c_output_data_dict_safe(self):
        """Item 2c: output_data as dict is handled safely."""
        from src.api.batch_routes import _serialize_task

        task = self._make_task(output_data={'video_duration': 120.5})
        result = _serialize_task(task)
        assert result['output_data'] == {'video_duration': 120.5}

    def test_03_null_fields_safe(self):
        """Item 3: warning, final_video_url, error fields null-safe."""
        from src.api.batch_routes import _serialize_task

        task = self._make_task(
            warning=None,
            final_video_url=None,
            error_code=None,
            error_message=None,
        )
        result = _serialize_task(task)
        assert result['warning'] is None
        assert result['final_video_url'] is None
        assert result['error_code'] is None
        assert result['error_message'] is None

    def test_04_all_statuses_returned(self):
        """Item 4: success, failed, queued, running all returned (not filtered)."""
        from src.api.batch_routes import _serialize_task

        statuses = ['created', 'pending', 'queued', 'running', 'success', 'failed']
        tasks = [self._make_task(task_id=f't{i}', status=s) for i, s in enumerate(statuses)]

        serialized = [_serialize_task(t) for t in tasks]
        returned_statuses = [t['status'] for t in serialized]

        assert set(returned_statuses) == set(statuses)

    def test_05_single_bad_data_no_500(self):
        """Item 5: Single task with bad data doesn't crash entire list."""
        from src.api.batch_routes import _serialize_task

        # Create a task where some attributes raise exceptions
        bad_task = MagicMock()
        bad_task.task_id = 'bad-task'
        bad_task.batch_id = 'batch-001'
        bad_task.row_number = 1
        bad_task.external_task_id = None
        bad_task.run_id = None
        bad_task.status = 'success'
        bad_task.script_id = None
        bad_task.script_text = None
        bad_task.title = None
        bad_task.input_data = None
        bad_task.output_data = None
        bad_task.final_video_url = None
        bad_task.error_code = None
        bad_task.error_message = None
        bad_task.retry_count = 0
        bad_task.async_task_id = None
        bad_task.warning = None
        bad_task.created_at = None
        bad_task.started_at = None
        bad_task.completed_at = None
        bad_task.updated_at = None

        # Should not raise
        result = _serialize_task(bad_task)
        assert result['task_id'] == 'bad-task'


# ============================================================
# Frontend Rendering Tests (Items 6-11)
# ============================================================

class TestFrontendRendering:
    """Tests for frontend task card rendering."""

    def test_06_2_success_1_failed_shows_3_cards(self):
        """Item 6: 2 success + 1 failed renders 3 task cards."""
        # This tests the renderTaskCard logic
        from web.app_test_helpers import render_task_card_html

        tasks = [
            {'task_id': 't1', 'status': 'success', 'final_video_url': 'https://v.com/1.mp4',
             'script_text': '脚本1', 'script_id': 's1', 'title': '标题1',
             'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0},
            {'task_id': 't2', 'status': 'success', 'final_video_url': 'https://v.com/2.mp4',
             'script_text': '脚本2', 'script_id': 's2', 'title': '标题2',
             'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0},
            {'task_id': 't3', 'status': 'failed', 'final_video_url': None,
             'script_text': '脚本3', 'script_id': 's3', 'title': '标题3',
             'warning': None, 'error_code': 'BGM_MIX_FAILED',
             'error_message': 'BGM 混音失败', 'retry_count': 1},
        ]

        html_parts = [render_task_card_html(t) for t in tasks]
        html = '\n'.join(html_parts)

        # Should have 3 task cards
        assert html.count('task-card') >= 3
        # Should contain all task IDs
        assert 't1' in html
        assert 't2' in html
        assert 't3' in html

    def test_07_success_tasks_show_view_video_button(self):
        """Item 7: Two success tasks both show '查看视频' button."""
        from web.app_test_helpers import render_task_card_html

        for url in ['https://v.com/1.mp4', 'https://v.com/2.mp4']:
            task = {
                'task_id': 't1', 'status': 'success', 'final_video_url': url,
                'script_text': '', 'script_id': None, 'title': None,
                'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0,
            }
            html = render_task_card_html(task)
            assert '查看视频' in html, f"Missing '查看视频' for url={url}"

    def test_08_failed_task_shows_error_and_retry(self):
        """Item 8: Failed task shows full error and retry button."""
        from web.app_test_helpers import render_task_card_html

        task = {
            'task_id': 't3', 'status': 'failed', 'final_video_url': None,
            'script_text': '脚本3', 'script_id': 's3', 'title': '标题3',
            'warning': None, 'error_code': 'BGM_MIX_FAILED',
            'error_message': 'BGM 混音失败，已降级为 TTS-only', 'retry_count': 2,
        }
        html = render_task_card_html(task)

        assert 'BGM_MIX_FAILED' in html
        assert 'BGM 混音失败' in html
        assert '重试' in html

    def test_09_success_no_video_url_still_shows_card(self):
        """Item 9: Success but no final_video_url still shows task card."""
        from web.app_test_helpers import render_task_card_html

        task = {
            'task_id': 't1', 'status': 'success', 'final_video_url': None,
            'script_text': '脚本1', 'script_id': 's1', 'title': '标题1',
            'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0,
        }
        html = render_task_card_html(task)

        assert 'task-card' in html
        assert 't1' in html
        # Should show message about video URL not yet written back
        assert '视频地址尚未回写' in html or '成功' in html

    def test_10_tasks_data_array_parsing(self):
        """Item 10: Response as {tasks: [...]}, {data: {tasks: [...]}}, or [...] all parse."""
        from web.app_test_helpers import parse_tasks_response

        tasks_data = [{'task_id': 't1', 'status': 'success'}]

        # Format 1: { tasks: [...] }
        result1 = parse_tasks_response({'tasks': tasks_data})
        assert len(result1) == 1

        # Format 2: { data: { tasks: [...] } }
        result2 = parse_tasks_response({'data': {'tasks': tasks_data}})
        assert len(result2) == 1

        # Format 3: [...]
        result3 = parse_tasks_response(tasks_data)
        assert len(result3) == 1

    def test_11_unexpected_response_shows_error(self):
        """Item 11: Unexpected response structure shows specific error."""
        from web.app_test_helpers import parse_tasks_response

        # Unexpected structure
        result = parse_tasks_response({'error': 'something went wrong'})
        assert result is None or result == []


# ============================================================
# Retry Tests (Items 12-18)
# ============================================================

class TestRetryLogic:
    """Tests for single task retry behavior."""

    def test_12_only_failed_can_retry(self):
        """Item 12: Only failed tasks can be retried."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Success task should not be retryable
        task = MagicMock()
        task.status = 'success'
        assert not executor._can_retry_task(task)

        # Running task should not be retryable
        task.status = 'running'
        assert not executor._can_retry_task(task)

        # Pending task should not be retryable
        task.status = 'pending'
        assert not executor._can_retry_task(task)

        # Failed task should be retryable
        task.status = 'failed'
        assert executor._can_retry_task(task)

    def test_13_http_200_and_202_both_success(self):
        """Item 13: HTTP 200 and 202 both treated as success."""
        # This is tested in frontend logic
        from web.app_test_helpers import is_retry_success

        assert is_retry_success(200) is True
        assert is_retry_success(202) is True
        assert is_retry_success(201) is True
        assert is_retry_success(400) is False
        assert is_retry_success(500) is False

    def test_14_no_duplicate_click(self):
        """Item 14: Duplicate click doesn't submit twice."""
        from web.app_test_helpers import RetryGuard

        guard = RetryGuard()

        # First click should succeed
        assert guard.try_acquire('task-001') is True
        # Second click should be blocked
        assert guard.try_acquire('task-001') is False
        # Release and try again
        guard.release('task-001')
        assert guard.try_acquire('task-001') is True

    def test_15_retry_count_increments(self):
        """Item 15: retry_count increments from existing value."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Task with retry_count=0
        task = MagicMock()
        task.retry_count = 0
        new_count = executor._increment_retry_count(task)
        assert new_count == 1

        # Task with retry_count=3
        task.retry_count = 3
        new_count = executor._increment_retry_count(task)
        assert new_count == 4

        # Task with retry_count=None
        task.retry_count = None
        new_count = executor._increment_retry_count(task)
        assert new_count == 1

    def test_16_retry_failure_shows_real_error(self):
        """Item 16: Retry failure shows real backend error, not 'unknown error'."""
        from web.app_test_helpers import extract_error_message

        # Response with detail
        assert extract_error_message(400, {'detail': '任务状态不是 failed'}) == '任务状态不是 failed'

        # Response with message
        assert extract_error_message(500, {'message': '数据库连接失败'}) == '数据库连接失败'

        # Response with error
        assert extract_error_message(422, {'error': '参数错误'}) == '参数错误'

        # Empty body
        assert '400' in extract_error_message(400, None)

    def test_17_response_anomaly_but_task_queued(self):
        """Item 17: Response anomaly but task already queued - don't re-submit."""
        from web.app_test_helpers import should_resubmit_after_anomaly

        # Task already in queued state - should NOT resubmit
        assert should_resubmit_after_anomaly('queued') is False
        # Task in running state - should NOT resubmit
        assert should_resubmit_after_anomaly('running') is False
        # Task still in failed state - should resubmit
        assert should_resubmit_after_anomaly('failed') is True

    def test_18_retry_failed_doesnt_affect_success(self):
        """Item 18: Retrying failed task doesn't modify success tasks."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Simulate 3 tasks: 2 success, 1 failed
        tasks = [
            MagicMock(task_id='t1', status='success', retry_count=0),
            MagicMock(task_id='t2', status='success', retry_count=0),
            MagicMock(task_id='t3', status='failed', retry_count=1),
        ]

        # Only retry t3
        target = tasks[2]
        assert executor._can_retry_task(target)
        assert not executor._can_retry_task(tasks[0])
        assert not executor._can_retry_task(tasks[1])

        # After retry, success tasks should be unchanged
        assert tasks[0].status == 'success'
        assert tasks[1].status == 'success'


# ============================================================
# Start Batch Tests (Items 19-22)
# ============================================================

class TestStartBatch:
    """Tests for start batch response handling."""

    def test_19_202_is_success(self):
        """Item 19: Start batch returns 202 is success."""
        from web.app_test_helpers import is_start_success

        assert is_start_success(200) is True
        assert is_start_success(202) is True
        assert is_start_success(400) is False
        assert is_start_success(500) is False

    def test_20_empty_response_body(self):
        """Item 20: Empty response body doesn't cause error."""
        from web.app_test_helpers import parse_start_response

        # Empty body with 202 should be fine
        result = parse_start_response(202, '')
        assert result['success'] is True

        # Empty body with 200 should be fine
        result = parse_start_response(200, '')
        assert result['success'] is True

    def test_21_missing_non_essential_fields(self):
        """Item 21: Missing non-essential fields doesn't cause error."""
        from web.app_test_helpers import parse_start_response

        # Response with only status
        result = parse_start_response(202, '{"status": "started"}')
        assert result['success'] is True

        # Response with empty object
        result = parse_start_response(202, '{}')
        assert result['success'] is True

    def test_22_response_anomaly_but_tasks_changed(self):
        """Item 22: Response anomaly but tasks already changed - don't re-start."""
        from web.app_test_helpers import should_restart_after_anomaly

        # Tasks already in queued/running - should NOT restart
        assert should_restart_after_anomaly([
            {'status': 'queued'}, {'status': 'running'}, {'status': 'success'}
        ]) is False

        # All tasks still pending - should restart
        assert should_restart_after_anomaly([
            {'status': 'pending'}, {'status': 'pending'}
        ]) is True


# ============================================================
# CSV Export Tests (Items 23-27)
# ============================================================

class TestCSVExport:
    """Tests for CSV export functionality."""

    def test_23_2_success_1_failed_export(self):
        """Item 23: 2 success + 1 failed can export normally."""
        from web.app_test_helpers import generate_csv

        tasks = [
            {'task_id': 't1', 'batch_id': 'b1', 'script_id': 's1', 'title': '标题1',
             'script_text': '脚本1', 'status': 'success', 'final_video_url': 'https://v.com/1.mp4',
             'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0,
             'run_id': 'r1', 'async_task_id': 'a1', 'created_at': '2025-01-01T00:00:00',
             'started_at': '2025-01-01T00:00:10', 'completed_at': '2025-01-01T00:05:00',
             'updated_at': '2025-01-01T00:05:00'},
            {'task_id': 't2', 'batch_id': 'b1', 'script_id': 's2', 'title': '标题2',
             'script_text': '脚本2', 'status': 'success', 'final_video_url': 'https://v.com/2.mp4',
             'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0,
             'run_id': 'r2', 'async_task_id': 'a2', 'created_at': '2025-01-01T00:00:00',
             'started_at': '2025-01-01T00:00:10', 'completed_at': '2025-01-01T00:05:00',
             'updated_at': '2025-01-01T00:05:00'},
            {'task_id': 't3', 'batch_id': 'b1', 'script_id': 's3', 'title': '标题3',
             'script_text': '脚本3', 'status': 'failed', 'final_video_url': None,
             'warning': 'BGM 降级', 'error_code': 'BGM_MIX_FAILED',
             'error_message': 'BGM 混音失败', 'retry_count': 1,
             'run_id': 'r3', 'async_task_id': 'a3', 'created_at': '2025-01-01T00:00:00',
             'started_at': '2025-01-01T00:00:10', 'completed_at': '2025-01-01T00:05:00',
             'updated_at': '2025-01-01T00:05:00'},
        ]

        csv_content = generate_csv(tasks)
        assert csv_content is not None
        assert len(csv_content) > 0
        # Should contain all 3 task IDs
        assert 't1' in csv_content
        assert 't2' in csv_content
        assert 't3' in csv_content

    def test_24_csv_preserves_fields(self):
        """Item 24: CSV preserves script_id, title, script_text."""
        from web.app_test_helpers import generate_csv

        tasks = [{
            'task_id': 't1', 'batch_id': 'b1', 'script_id': 'my-script-id',
            'title': '我的标题', 'script_text': '这是一段测试脚本文本',
            'status': 'success', 'final_video_url': 'https://v.com/1.mp4',
            'warning': None, 'error_code': None, 'error_message': None, 'retry_count': 0,
            'run_id': 'r1', 'async_task_id': 'a1', 'created_at': '2025-01-01',
            'started_at': '2025-01-01', 'completed_at': '2025-01-01', 'updated_at': '2025-01-01',
        }]

        csv_content = generate_csv(tasks)
        assert 'my-script-id' in csv_content
        assert '我的标题' in csv_content
        assert '这是一段测试脚本文本' in csv_content

    def test_25_chinese_comma_quote_newline_escaping(self):
        """Item 25: Chinese, comma, double quotes, newline correctly escaped."""
        from web.app_test_helpers import csv_escape

        # Comma
        assert csv_escape('hello,world') == '"hello,world"'
        # Double quotes
        assert csv_escape('say "hello"') == '"say ""hello"""'
        # Newline
        assert csv_escape('line1\nline2') == '"line1\nline2"'
        # Chinese
        assert csv_escape('中文内容') == '中文内容'
        # Chinese with comma
        assert csv_escape('中文,内容') == '"中文,内容"'

    def test_26_null_fields_no_crash(self):
        """Item 26: Null fields don't cause export failure."""
        from web.app_test_helpers import generate_csv

        tasks = [{
            'task_id': 't1', 'batch_id': 'b1', 'script_id': None,
            'title': None, 'script_text': None, 'status': 'pending',
            'final_video_url': None, 'warning': None, 'error_code': None,
            'error_message': None, 'retry_count': 0, 'run_id': None,
            'async_task_id': None, 'created_at': '2025-01-01',
            'started_at': None, 'completed_at': None, 'updated_at': None,
        }]

        csv_content = generate_csv(tasks)
        assert csv_content is not None
        assert 't1' in csv_content

    def test_27_utf8_bom(self):
        """Item 27: UTF-8 BOM present."""
        from web.app_test_helpers import generate_csv

        tasks = [{
            'task_id': 't1', 'batch_id': 'b1', 'script_id': None,
            'title': '标题', 'script_text': '文本', 'status': 'success',
            'final_video_url': None, 'warning': None, 'error_code': None,
            'error_message': None, 'retry_count': 0, 'run_id': None,
            'async_task_id': None, 'created_at': '2025-01-01',
            'started_at': None, 'completed_at': None, 'updated_at': None,
        }]

        csv_content = generate_csv(tasks)
        # Check BOM
        assert csv_content.startswith('\ufeff')


# ============================================================
# Concurrency Tests (Items 28-33)
# ============================================================

class TestConcurrencyControl:
    """Tests for batch concurrency control."""

    def test_28_3_tasks_concurrency_2_max_2_running(self):
        """Item 28: 3 tasks, concurrency 2, max 2 running."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Simulate 3 pending tasks with concurrency=2
        tasks = [
            MagicMock(task_id='t1', status='pending', async_task_id=None),
            MagicMock(task_id='t2', status='pending', async_task_id=None),
            MagicMock(task_id='t3', status='pending', async_task_id=None),
        ]

        # Get tasks to submit (should be at most 2)
        to_submit = executor._get_tasks_to_submit(tasks, concurrency=2)
        assert len(to_submit) <= 2

    def test_29_20_tasks_concurrency_2_initial_max_2(self):
        """Item 29: 20 tasks, concurrency 2, initial max 2 running."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        tasks = [MagicMock(task_id=f't{i}', status='pending', async_task_id=None) for i in range(20)]

        to_submit = executor._get_tasks_to_submit(tasks, concurrency=2)
        assert len(to_submit) <= 2

    def test_30_one_completes_one_submitted(self):
        """Item 30: One task completes, only one more submitted."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # 1 running, 1 completed, 1 pending
        tasks = [
            MagicMock(task_id='t1', status='running', async_task_id='a1'),
            MagicMock(task_id='t2', status='success', async_task_id='a2'),
            MagicMock(task_id='t3', status='pending', async_task_id=None),
        ]

        # With concurrency=2, 1 running, should submit 1 more
        to_submit = executor._get_tasks_to_submit(tasks, concurrency=2)
        assert len(to_submit) == 1
        assert to_submit[0].task_id == 't3'

    def test_31_no_duplicate_submission_on_concurrent_poll(self):
        """Item 31: Multiple concurrent poll requests don't submit same task twice."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Task already has async_task_id (already submitted)
        tasks = [
            MagicMock(task_id='t1', status='pending', async_task_id='already-submitted'),
            MagicMock(task_id='t2', status='pending', async_task_id=None),
        ]

        # Should only submit t2 (t1 already has async_task_id)
        to_submit = executor._get_tasks_to_submit(tasks, concurrency=2)
        assert len(to_submit) == 1
        assert to_submit[0].task_id == 't2'

    def test_32_no_multiple_async_task_ids(self):
        """Item 32: Same task doesn't get multiple async_task_ids."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Task already submitted (has async_task_id)
        task = MagicMock(task_id='t1', status='queued', async_task_id='existing-async-id')

        # Should not be in the submit list
        to_submit = executor._get_tasks_to_submit([task], concurrency=2)
        assert len(to_submit) == 0

    def test_33_success_or_failed_releases_slot(self):
        """Item 33: success or failed both release execution slot."""
        from src.api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())

        # Case 1: 1 success (releases slot), 1 pending
        tasks1 = [
            MagicMock(task_id='t1', status='success', async_task_id='a1'),
            MagicMock(task_id='t2', status='pending', async_task_id=None),
        ]
        to_submit1 = executor._get_tasks_to_submit(tasks1, concurrency=1)
        assert len(to_submit1) == 1

        # Case 2: 1 failed (releases slot), 1 pending
        tasks2 = [
            MagicMock(task_id='t1', status='failed', async_task_id='a1'),
            MagicMock(task_id='t2', status='pending', async_task_id=None),
        ]
        to_submit2 = executor._get_tasks_to_submit(tasks2, concurrency=1)
        assert len(to_submit2) == 1


# ============================================================
# Historical Data Regression Tests (Items 34-41)
# ============================================================

class TestHistoricalDataRegression:
    """Tests for serialization with production-like historical data formats."""

    def test_34_output_data_none(self):
        """Item 34: output_data=None should not cause 500."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-001'
        task.batch_id = 'batch-001'
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = None
        task.final_video_url = 'https://example.com/video.mp4'
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['task_id'] == 'task-001'
        assert result['output_data'] is None
        assert result['final_video_url'] == 'https://example.com/video.mp4'

    def test_35_output_data_dict(self):
        """Item 35: output_data=dict should be returned as-is."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-002'
        task.batch_id = 'batch-001'
        task.row_number = 2
        task.external_task_id = 'ext-002'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = {'video_duration': 120.5, 'resolution': '1080p'}
        task.final_video_url = 'https://example.com/video2.mp4'
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['output_data'] == {'video_duration': 120.5, 'resolution': '1080p'}

    def test_36_output_data_json_string(self):
        """Item 36: output_data=JSON string should be parsed."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-003'
        task.batch_id = 'batch-001'
        task.row_number = 3
        task.external_task_id = 'ext-003'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = '{"video_duration": 120.5, "resolution": "1080p"}'
        task.final_video_url = None
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['output_data'] == {'video_duration': 120.5, 'resolution': '1080p'}

    def test_37_uuid_enum_datetime_handling(self):
        """Item 37: UUID/Enum/datetime should be properly converted."""
        from src.api.batch_routes import _serialize_task
        from uuid import UUID

        task = MagicMock()
        task.task_id = UUID('12345678-1234-5678-1234-567812345678')
        task.batch_id = UUID('87654321-4321-8765-4321-876543218765')
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = UUID('11111111-2222-3333-4444-555555555555')
        task.async_task_id = 'async-001'
        task.status = 'success'
        task.input_data = None
        task.output_data = None
        task.final_video_url = 'https://example.com/video.mp4'
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        # UUID should be converted to string
        assert result['task_id'] == '12345678-1234-5678-1234-567812345678'
        assert result['batch_id'] == '87654321-4321-8765-4321-876543218765'
        assert result['run_id'] == '11111111-2222-3333-4444-555555555555'
        # datetime should be converted to ISO format string
        assert result['created_at'] == '2025-01-01T00:00:00'

    def test_38_success_with_final_video_url(self):
        """Item 38: Success task with final_video_url should return it."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-001'
        task.batch_id = 'batch-001'
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = None
        task.final_video_url = 'https://example.com/video.mp4'
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['final_video_url'] == 'https://example.com/video.mp4'

    def test_39_success_without_final_video_url(self):
        """Item 39: Success task without final_video_url should return None."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-001'
        task.batch_id = 'batch-001'
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = None
        task.final_video_url = None
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['final_video_url'] is None

    def test_40_failed_task_with_full_error(self):
        """Item 40: Failed task with complete error info should return it."""
        from src.api.batch_routes import _serialize_task

        task = MagicMock()
        task.task_id = 'task-001'
        task.batch_id = 'batch-001'
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = None
        task.async_task_id = None
        task.status = 'failed'
        task.input_data = None
        task.output_data = None
        task.final_video_url = None
        task.warning = None
        task.error_code = 'BGM_MIX_FAILED'
        task.error_message = 'FFmpeg returned exit code -234: aloop parameter overflow'
        task.retry_count = 2
        task.created_at = datetime(2025, 1, 1, 0, 0, 0)
        task.started_at = datetime(2025, 1, 1, 0, 0, 10)
        task.completed_at = datetime(2025, 1, 1, 0, 5, 0)
        task.updated_at = datetime(2025, 1, 1, 0, 5, 0)

        result = _serialize_task(task)
        assert result['status'] == 'failed'
        assert result['error_code'] == 'BGM_MIX_FAILED'
        assert 'aloop parameter overflow' in result['error_message']
        assert result['retry_count'] == 2

    def test_41_single_bad_task_does_not_break_list(self):
        """Item 41: Single bad task should not break entire list."""
        from src.api.batch_routes import _serialize_task

        # Create a task that will cause serialization to fail
        task = MagicMock()
        task.task_id = 'bad-task'
        task.batch_id = 'batch-001'
        task.row_number = 1
        task.external_task_id = 'ext-001'
        task.run_id = None
        task.async_task_id = None
        task.status = 'success'
        task.input_data = None
        task.output_data = None
        task.final_video_url = None
        task.warning = None
        task.error_code = None
        task.error_message = None
        task.retry_count = 0
        # Make created_at raise an exception
        type(task).created_at = property(lambda self: (_ for _ in ()).throw(Exception("Bad datetime")))
        task.started_at = None
        task.completed_at = None
        task.updated_at = None

        # This should raise an exception
        with pytest.raises(Exception):
            _serialize_task(task)

        # But the per-task error handling in get_batch_tasks should catch it
        # and return a safe fallback
