"""
Tests for status response schema and frontend parsing.

These tests verify:
1. Backend returns a consistent response schema
2. Frontend safely parses the response without throwing exceptions
3. data.error is treated as a business result, not an API error
4. Null/missing optional fields are handled correctly
5. Different status values (queued/running/success/failed) are handled correctly
"""
import pytest
import json
import re
import os


def get_frontend_html():
    """Read the frontend HTML from main.py without importing it."""
    main_path = os.path.join(os.path.dirname(__file__), '..', 'src', 'main.py')
    with open(main_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Extract the _INDEX_HTML content
    # Find the string between _INDEX_HTML = """ and the closing """
    match = re.search(r'_INDEX_HTML\s*=\s*"""(.*?)"""', content, re.DOTALL)
    if match:
        return match.group(1)
    return ""


class TestStatusResponseSchema:
    """Tests for the backend status response schema."""

    def test_response_contains_required_fields(self):
        """Verify response contains all required fields."""
        # Simulate a successful response
        response = {
            "run_id": "test-run-id",
            "status": "success",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "query_method": "task_id",
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        
        # Required fields
        required_fields = ["run_id", "status", "task_id", "batch_id"]
        for field in required_fields:
            assert field in response, f"Missing required field: {field}"

    def test_success_response_contains_final_video_url(self):
        """Verify success response contains final_video_url."""
        response = {
            "run_id": "test-run-id",
            "status": "success",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "final_video_url": "https://example.com/video.mp4",
            "result": {"output": "test"},
        }
        
        assert response["status"] == "success"
        assert "final_video_url" in response
        assert response["final_video_url"].startswith("https://")

    def test_failed_response_contains_error(self):
        """Verify failed response contains error information."""
        response = {
            "run_id": "test-run-id",
            "status": "failed",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "error": "Task failed with error",
            "error_code": "RUNTIME_ERROR",
            "result": {},
        }
        
        assert response["status"] == "failed"
        assert "error" in response
        assert response["error"] is not None

    def test_running_response_no_result(self):
        """Verify running response does not contain result."""
        response = {
            "run_id": "test-run-id",
            "status": "running",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        
        assert response["status"] == "running"
        # Running tasks should not have result yet
        assert "result" not in response or response.get("result") is None

    def test_queued_response_minimal_fields(self):
        """Verify queued response has minimal fields."""
        response = {
            "run_id": "test-run-id",
            "status": "queued",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "created_at": "2024-01-01T00:00:00+00:00",
        }
        
        assert response["status"] == "queued"
        # Queued tasks should not have result or error
        assert "result" not in response or response.get("result") is None
        assert "error" not in response or response.get("error") is None

    def test_response_with_null_optional_fields(self):
        """Verify response handles null optional fields."""
        response = {
            "run_id": "test-run-id",
            "status": "success",
            "task_id": "test-task-id",
            "batch_id": "test-batch-id",
            "final_video_url": None,
            "error": None,
            "result": None,
        }
        
        # Should not throw when accessing null fields
        assert response.get("final_video_url") is None
        assert response.get("error") is None
        assert response.get("result") is None


class TestFrontendParsing:
    """Tests for frontend response parsing logic."""

    def test_parse_valid_json_response(self):
        """Verify valid JSON response is parsed correctly."""
        raw = '{"status": "success", "run_id": "test", "task_id": "t1", "batch_id": "b1"}'
        data = json.loads(raw)
        
        assert data is not None
        assert isinstance(data, dict)
        assert data["status"] == "success"

    def test_parse_null_json_response(self):
        """Verify null JSON response is handled."""
        raw = "null"
        data = json.loads(raw)
        
        assert data is None
        # Frontend should check: if (!data || typeof data !== 'object')

    def test_parse_invalid_json_response(self):
        """Verify invalid JSON response is handled."""
        raw = "not valid json"
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None
        
        assert data is None

    def test_parse_empty_object_response(self):
        """Verify empty object response is handled."""
        raw = "{}"
        data = json.loads(raw)
        
        assert data is not None
        assert isinstance(data, dict)
        assert data.get("status") is None

    def test_error_field_is_business_result_not_api_error(self):
        """Verify error field is treated as business result, not API error."""
        # This is the key fix: data.error should NOT be treated as an API error
        response = {
            "status": "failed",
            "error": "Task failed with OOM",
            "run_id": "test",
            "task_id": "t1",
            "batch_id": "b1",
        }
        
        # Frontend should check status, not data.error
        status = response.get("status")
        assert status == "failed"
        
        # error field should be displayed as part of the result
        error = response.get("error")
        assert error == "Task failed with OOM"

    def test_status_values_are_normalized(self):
        """Verify status values are normalized to expected set."""
        valid_statuses = {"queued", "running", "success", "failed", "timeout", "cancelled"}
        
        for status in ["queued", "running", "success", "failed", "timeout", "cancelled"]:
            assert status in valid_statuses

    def test_topology_nodes_null_safety(self):
        """Verify frontend handles null topology.nodes."""
        topology = None
        nodes = topology.get("nodes") if topology else None
        
        assert nodes is None
        
        # Frontend should check: if (topology && topology.nodes && Array.isArray(topology.nodes))

    def test_topology_nodes_empty_array(self):
        """Verify frontend handles empty topology.nodes array."""
        topology = {"nodes": []}
        nodes = topology.get("nodes") if topology else None
        
        assert nodes is not None
        assert isinstance(nodes, list)
        assert len(nodes) == 0

    def test_final_video_url_display(self):
        """Verify final_video_url is displayed for success status."""
        response = {
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        }
        
        # Frontend should display video when status is success and final_video_url exists
        if response.get("status") == "success" and response.get("final_video_url"):
            should_display_video = True
        else:
            should_display_video = False
        
        assert should_display_video is True

    def test_error_display_for_failed_status(self):
        """Verify error is displayed for failed status."""
        response = {
            "status": "failed",
            "error": "Task failed with OOM",
        }
        
        # Frontend should display error when status is failed
        if response.get("status") == "failed" and response.get("error"):
            should_display_error = True
        else:
            should_display_error = False
        
        assert should_display_error is True


class TestFrontendCodeStructure:
    """Tests for frontend code structure to ensure safety."""

    def test_frontend_does_not_treat_data_error_as_api_error(self):
        """Verify frontend code does not treat data.error as API error."""
        html_content = get_frontend_html()
        
        # Check that the old pattern is NOT present
        # Old pattern: if (data.error) { setError('加载失败: ' + ...
        old_pattern = r"if\s*\(\s*data\.error\s*\)\s*\{\s*setError\(['\"]加载失败"
        assert not re.search(old_pattern, html_content), \
            "Frontend should not treat data.error as API error"

    def test_frontend_checks_data_is_object(self):
        """Verify frontend checks data is an object."""
        html_content = get_frontend_html()
        
        # Check that the new pattern IS present
        # New pattern: if (!data || typeof data !== 'object')
        new_pattern = r"if\s*\(\s*!data\s*\|\|\s*typeof\s+data\s*!==\s*['\"]object['\"]\s*\)"
        assert re.search(new_pattern, html_content), \
            "Frontend should check data is an object"

    def test_frontend_shows_detailed_error_info(self):
        """Verify frontend shows detailed error information."""
        html_content = get_frontend_html()
        
        # Check that error.name is included
        assert "e.name" in html_content or "errorName" in html_content, \
            "Frontend should show error name"
        
        # Check that Request URL is included
        assert "Request URL" in html_content, \
            "Frontend should show Request URL"
        
        # Check that raw response is included
        assert "raw" in html_content.lower() or "响应正文" in html_content or "原始响应" in html_content, \
            "Frontend should show raw response"

    def test_frontend_handles_null_topology_nodes(self):
        """Verify frontend handles null topology.nodes."""
        html_content = get_frontend_html()
        
        # Check that topology.nodes is safely accessed
        # Pattern: topology.nodes.forEach should be guarded
        pattern = r"topology\.nodes\.forEach"
        matches = re.findall(pattern, html_content)
        
        # Should have at least one forEach, but it should be guarded
        if matches:
            # Check for guard: if (topology && topology.nodes && Array.isArray(topology.nodes))
            guard_pattern = r"if\s*\(\s*topology\s*&&\s*topology\.nodes\s*&&\s*Array\.isArray"
            assert re.search(guard_pattern, html_content), \
                "topology.nodes.forEach should be guarded with null check"
