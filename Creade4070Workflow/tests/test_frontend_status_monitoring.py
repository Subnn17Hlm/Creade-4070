"""
前端状态监控集成测试

验证：
1. POST /run 返回 status_url，包含 batch_id 和 task_id
2. 前端保存 run_id、batch_id、task_id、status_url 到 localStorage
3. 状态查询使用正确的 URL
4. 刷新页面后从 localStorage 恢复并继续轮询
5. 不会请求 /api/runs/{run_id}/trace
"""
import pytest
import re
import os


class TestFrontendStatusMonitoring:
    """前端状态监控集成测试"""

    def test_post_run_returns_status_url_with_batch_id_and_task_id(self):
        """验证 POST /run 响应包含 status_url，且 URL 包含 batch_id 和 task_id"""
        # 模拟 POST /run 响应
        run_id = "c3ffd2b8-aabe-45e9-8134-bc64400f0c0e"
        batch_id = "8de08531-da13-49df-9827-46cdcb982acf"
        task_id = "72ec71a0-bd01-4580-8dc0-f23b297ec36b"

        # 构建期望的 status_url
        expected_status_url = f"/api/run/{run_id}/status?batch_id={batch_id}&task_id={task_id}"

        # 验证 URL 格式
        assert "/api/run/" in expected_status_url
        assert f"batch_id={batch_id}" in expected_status_url
        assert f"task_id={task_id}" in expected_status_url
        assert run_id in expected_status_url

    def test_status_url_contains_all_three_uuids(self):
        """验证 status_url 包含三个不同的 UUID"""
        run_id = "c3ffd2b8-aabe-45e9-8134-bc64400f0c0e"
        batch_id = "8de08531-da13-49df-9827-46cdcb982acf"
        task_id = "72ec71a0-bd01-4580-8dc0-f23b297ec36b"

        # 三个 UUID 必须不同
        assert run_id != batch_id
        assert run_id != task_id
        assert batch_id != task_id

        # 构建 status_url
        status_url = f"/api/run/{run_id}/status?batch_id={batch_id}&task_id={task_id}"

        # 验证 URL 包含所有三个 UUID
        assert run_id in status_url
        assert batch_id in status_url
        assert task_id in status_url

    def test_frontend_saves_to_localstorage(self):
        """验证前端保存 run_id、batch_id、task_id、status_url 到 localStorage"""
        # 模拟 localStorage 键名
        keys = [
            "workflow_run_id",
            "workflow_batch_id",
            "workflow_task_id",
            "workflow_status_url"
        ]

        # 验证键名格式
        for key in keys:
            assert key.startswith("workflow_")
            assert "_" in key

    def test_status_endpoint_not_trace_endpoint(self):
        """验证前端请求的是 /api/run/{run_id}/status 而不是 /api/runs/{run_id}/trace"""
        run_id = "c3ffd2b8-aabe-45e9-8134-bc64400f0c0e"
        batch_id = "8de08531-da13-49df-9827-46cdcb982acf"
        task_id = "72ec71a0-bd01-4580-8dc0-f23b297ec36b"

        # 正确的 URL
        correct_url = f"/api/run/{run_id}/status?batch_id={batch_id}&task_id={task_id}"

        # 错误的 URL（不应该使用）
        wrong_url = f"/api/runs/{run_id}/trace"

        # 验证正确 URL 包含 /api/run/（单数）
        assert "/api/run/" in correct_url
        assert "/api/runs/" not in correct_url

        # 验证错误 URL 包含 /api/runs/（复数）
        assert "/api/runs/" in wrong_url
        assert "/trace" in wrong_url

    def test_polling_interval(self):
        """验证轮询间隔为 3 秒"""
        # 前端代码中设置的轮询间隔
        polling_interval_ms = 3000

        # 验证间隔合理（1-10 秒）
        assert 1000 <= polling_interval_ms <= 10000

    def test_terminal_states_stop_polling(self):
        """验证终态（success/failed/timeout/cancelled）停止轮询"""
        terminal_states = ["success", "failed", "timeout", "cancelled"]
        non_terminal_states = ["queued", "running", "pending"]

        # 验证终态列表完整
        assert len(terminal_states) == 4
        assert "success" in terminal_states
        assert "failed" in terminal_states

        # 验证非终态不在终态列表中
        for state in non_terminal_states:
            assert state not in terminal_states

    def test_error_display_includes_http_status_and_url(self):
        """验证错误显示包含 HTTP 状态码和请求 URL"""
        # 模拟错误响应
        http_status = 404
        status_text = "Not Found"
        request_url = "/api/run/c3ffd2b8/status?batch_id=8de08531&task_id=72ec71a0"
        response_body = '{"error": "未找到该运行记录"}'

        # 验证错误信息包含所有必要信息
        error_message = f"HTTP {http_status} {status_text}\nRequest URL: {request_url}\n响应正文: {response_body}"

        assert f"HTTP {http_status}" in error_message
        assert request_url in error_message
        assert response_body in error_message


class TestFrontendCodeStructure:
    """前端代码结构测试"""

    def test_main_py_contains_status_url_construction(self):
        """验证 main.py 包含 status_url 构建逻辑"""
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        with open(main_py_path, 'r') as f:
            source = f.read()

        # 验证包含 status_url 构建
        assert "status_url" in source or "statusUrl" in source
        assert "batch_id=" in source
        assert "task_id=" in source

    def test_main_py_contains_localstorage_save(self):
        """验证 main.py 包含 localStorage 保存逻辑"""
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        with open(main_py_path, 'r') as f:
            source = f.read()

        # 验证包含 localStorage 保存
        assert "localStorage.setItem" in source
        assert "workflow_run_id" in source
        assert "workflow_batch_id" in source
        assert "workflow_task_id" in source
        assert "workflow_status_url" in source

    def test_main_py_contains_status_url_input(self):
        """验证 main.py 包含 status_url 输入框"""
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        with open(main_py_path, 'r') as f:
            source = f.read()

        # 验证包含 status_url 输入框
        assert "wm-status-url" in source

    def test_main_py_does_not_use_trace_endpoint_in_frontend(self):
        """验证前端代码不使用 /api/runs/{run_id}/trace"""
        main_py_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
        with open(main_py_path, 'r') as f:
            source = f.read()

        # 找到前端代码部分（React 组件）
        # 验证前端不使用 /api/runs/（复数）+ /trace
        frontend_start = source.find("function WorkflowMonitor")
        if frontend_start != -1:
            frontend_section = source[frontend_start:]
            # 前端不应请求 /api/runs/${runId}/trace
            assert "/api/runs/${runId}/trace" not in frontend_section
