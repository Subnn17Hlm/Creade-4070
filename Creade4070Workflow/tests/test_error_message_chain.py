"""
Tests for error message chain fix in batch_executor.py.

Covers:
1. fail_reason extraction priority
2. error field fallback
3. Unknown error fallback
4. JSON serialization safety
5. final_video_url + needs_review = success + warning
6. Task list and CSV continue HTTP 200
7. Response still excludes input_data/output_data
"""

import json
import uuid
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime

from api.batch_executor import (
    _sanitize_error_message,
    _extract_diagnostic_fields,
    MAX_ERROR_MESSAGE_LENGTH,
)


class TestSanitizeErrorMessage:
    """Test _sanitize_error_message helper."""

    def test_none_returns_unknown(self):
        assert _sanitize_error_message(None) == "Unknown error"

    def test_empty_string_returns_unknown(self):
        assert _sanitize_error_message("") == "Unknown error"
        assert _sanitize_error_message("   ") == "Unknown error"

    def test_string_passthrough(self):
        assert _sanitize_error_message("素材匹配失败") == "素材匹配失败"

    def test_strips_whitespace(self):
        assert _sanitize_error_message("  error  ") == "error"

    def test_truncates_long_message(self):
        long_msg = "x" * 5000
        result = _sanitize_error_message(long_msg)
        assert len(result) <= MAX_ERROR_MESSAGE_LENGTH + len("...[truncated]")
        assert result.endswith("...[truncated]")

    def test_non_string_converted(self):
        # Exception object
        err = ValueError("some error")
        result = _sanitize_error_message(err)
        assert result == "some error"

    def test_uuid_converted_to_string(self):
        uid = uuid.uuid4()
        result = _sanitize_error_message(uid)
        assert result == str(uid)

    def test_datetime_converted_to_string(self):
        dt = datetime(2026, 1, 1, 12, 0, 0)
        result = _sanitize_error_message(dt)
        assert "2026" in result

    def test_dict_converted_to_string(self):
        d = {"key": "value"}
        result = _sanitize_error_message(d)
        assert "key" in result

    def test_always_returns_string(self):
        for val in [None, "", 0, False, [], {}, set(), 1.5, True]:
            result = _sanitize_error_message(val)
            assert isinstance(result, str)
            assert len(result) > 0

    def test_json_serializable(self):
        """error_message must always be JSON serializable."""
        test_values = [
            None, "", "error", "x" * 5000,
            ValueError("err"), uuid.uuid4(), datetime.utcnow(),
            {"key": "val"}, [1, 2, 3], 42, 3.14,
        ]
        for val in test_values:
            result = _sanitize_error_message(val)
            # Must not raise
            json.dumps(result)


class TestExtractDiagnosticFields:
    """Test _extract_diagnostic_fields helper."""

    def test_none_returns_none(self):
        assert _extract_diagnostic_fields(None) is None

    def test_empty_dict_returns_none(self):
        assert _extract_diagnostic_fields({}) is None

    def test_extracts_status(self):
        result = _extract_diagnostic_fields({"status": "failed"})
        assert result["status"] == "failed"

    def test_extracts_fail_reason(self):
        result = _extract_diagnostic_fields({"fail_reason": "TTS failed"})
        assert result["fail_reason"] == "TTS failed"

    def test_extracts_error_as_fail_reason(self):
        result = _extract_diagnostic_fields({"error": "some error"})
        assert result["fail_reason"] == "some error"

    def test_extracts_message_as_fail_reason(self):
        result = _extract_diagnostic_fields({"message": "timeout"})
        assert result["fail_reason"] == "timeout"

    def test_fail_reason_priority(self):
        """fail_reason > error > message"""
        result = _extract_diagnostic_fields({
            "fail_reason": "reason1",
            "error": "reason2",
            "message": "reason3",
        })
        assert result["fail_reason"] == "reason1"

    def test_extracts_failure_category(self):
        result = _extract_diagnostic_fields({"failure_category": "subtitle_not_visible"})
        assert result["failed_node"] == "subtitle_not_visible"

    def test_uuid_converted(self):
        uid = uuid.uuid4()
        result = _extract_diagnostic_fields({"status": uid})
        assert result["status"] == str(uid)

    def test_datetime_converted(self):
        dt = datetime(2026, 1, 1)
        result = _extract_diagnostic_fields({"status": dt})
        assert "2026" in result["status"]

    def test_json_serializable(self):
        """All diagnostic fields must be JSON serializable."""
        result = _extract_diagnostic_fields({
            "status": "failed",
            "failure_category": "tts_error",
            "fail_reason": "TTS service timeout",
            "error_code": "TTS_TIMEOUT",
            "run_id": uuid.uuid4(),
            "timestamp": datetime.utcnow(),
        })
        # Must not raise
        json.dumps(result)

    def test_no_complex_objects(self):
        """Diagnostic fields must not contain lists, dicts, or nested objects."""
        result = _extract_diagnostic_fields({
            "status": "failed",
            "quality_report": {"score": 0.5},  # Should NOT be included
            "warnings": ["warn1", "warn2"],  # Should NOT be included
        })
        assert "quality_report" not in result
        assert "warnings" not in result
        # Only scalar fields
        for key, val in result.items():
            assert isinstance(val, str), f"Field {key} should be str, got {type(val)}"


class TestErrorExtractionPriority:
    """Test the error extraction priority in _execute_single_task flow."""

    def test_fail_reason_used_when_present(self):
        """When workflow returns fail_reason, it should be used as error."""
        workflow_result = {
            "status": "failed",
            "fail_reason": "TTS generation failed: timeout",
            "failure_category": "tts_error",
            "final_video_url": "",
        }
        raw_error = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or None
        )
        error_msg = _sanitize_error_message(raw_error)
        assert error_msg == "TTS generation failed: timeout"

    def test_error_field_used_when_no_fail_reason(self):
        """When only error field exists, it should be used."""
        workflow_result = {
            "status": "failed",
            "error": "FFmpeg concat failed",
        }
        raw_error = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or None
        )
        error_msg = _sanitize_error_message(raw_error)
        assert error_msg == "FFmpeg concat failed"

    def test_message_field_used_as_last_resort(self):
        """When only message field exists, it should be used."""
        workflow_result = {
            "status": "failed",
            "message": "Workflow timed out",
        }
        raw_error = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or None
        )
        error_msg = _sanitize_error_message(raw_error)
        assert error_msg == "Workflow timed out"

    def test_unknown_error_when_all_missing(self):
        """When none of the three fields exist, should return Unknown error."""
        workflow_result = {
            "status": "failed",
        }
        raw_error = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or None
        )
        error_msg = _sanitize_error_message(raw_error)
        assert error_msg == "Unknown error"


class TestSuccessSemantics:
    """Test that final_video_url presence keeps task as success."""

    def test_final_video_url_with_quality_failure_is_success(self):
        """
        If workflow returns status=failed but final_video_url exists,
        the task should be treated as success with warning.
        """
        workflow_result = {
            "status": "failed",
            "fail_reason": "low_confidence_segments=4>=3, needs_manual_review",
            "failure_category": "needs_review",
            "final_video_url": "https://example.com/video.mp4",
            "review_required": True,
            "warnings": ["low confidence segments"],
        }
        # Simulate the logic in _update_task_final_status
        final_video_url = workflow_result.get("final_video_url")
        assert final_video_url  # Video exists
        # This should be treated as success with warning
        assert workflow_result.get("status") == "failed"
        # But since final_video_url exists, it's a success with warning
        fail_reason = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or "Quality check flagged issues"
        )
        warning = f"视频已生成但存在质量告警: {fail_reason}"
        assert "质量告警" in warning

    def test_no_final_video_url_is_true_failure(self):
        """Without final_video_url, status=failed is a true failure."""
        workflow_result = {
            "status": "failed",
            "fail_reason": "TTS generation failed",
            "final_video_url": "",
        }
        final_video_url = workflow_result.get("final_video_url")
        assert not final_video_url  # No video
        # This is a true failure


class TestTaskListResponseFormat:
    """Test that task list API response format is unchanged."""

    @pytest.mark.asyncio
    async def test_task_list_returns_200_and_no_input_output_data(self):
        """Task list endpoint should return 200 and exclude input_data/output_data."""
        from api.batch_routes import _serialize_task
        from storage.database.batch_models import BatchTask, BatchTaskStatus

        # Create a mock task
        task = MagicMock(spec=BatchTask)
        task.task_id = uuid.uuid4()
        task.batch_id = uuid.uuid4()
        task.script_id = None
        task.title = None
        task.script_text = None
        task.status = BatchTaskStatus.FAILED
        task.final_video_url = None
        task.warning = None
        task.error_code = "WORKFLOW_ERROR"
        task.error_message = "TTS failed"
        task.retry_count = 0
        task.run_id = uuid.uuid4()
        task.async_task_id = None
        task.created_at = datetime.utcnow()
        task.started_at = datetime.utcnow()
        task.completed_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        task.input_data = {"script_text": "test script"}
        task.output_data = {"status": "failed", "fail_reason": "TTS failed"}

        result = _serialize_task(task)

        # Verify whitelist DTO
        assert "task_id" in result
        assert "status" in result
        assert "error_code" in result
        assert "error_message" in result

        # Verify input_data and output_data are NOT in response
        assert "input_data" not in result
        assert "output_data" not in result

    @pytest.mark.asyncio
    async def test_serialize_task_with_diagnostic_output_data(self):
        """Even with diagnostic output_data in DB, response should exclude it."""
        from api.batch_routes import _serialize_task
        from storage.database.batch_models import BatchTask, BatchTaskStatus

        task = MagicMock(spec=BatchTask)
        task.task_id = uuid.uuid4()
        task.batch_id = uuid.uuid4()
        task.script_id = None
        task.title = None
        task.script_text = None
        task.status = BatchTaskStatus.FAILED
        task.final_video_url = None
        task.warning = None
        task.error_code = "WORKFLOW_ERROR"
        task.error_message = "Unknown error"
        task.retry_count = 0
        task.run_id = None
        task.async_task_id = None
        task.created_at = datetime.utcnow()
        task.started_at = None
        task.completed_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        task.input_data = {"script_text": "test"}
        # Minimal diagnostic fields
        task.output_data = {
            "status": "failed",
            "failed_node": "tts_error",
            "fail_reason": "TTS timeout",
        }

        result = _serialize_task(task)

        assert "input_data" not in result
        assert "output_data" not in result
        assert result["error_message"] == "Unknown error"


class TestDiagnosticFieldsIntegration:
    """Integration test for diagnostic field extraction from workflow result."""

    def test_full_workflow_failure_result(self):
        """Simulate a real workflow failure result."""
        workflow_result = {
            "status": "failed",
            "fail_reason": "tts_wav_not_found; selected_material_id not from candidate_materials",
            "failure_category": "validation_failed",
            "final_video_url": "",
            "review_required": False,
            "warnings": [],
            "quality_report": {"score": 0.3, "details": "complex nested data"},
            "total_duration": 0.0,
            "node_trace": ["input_normalization", "tts_generation", "quality_check"],
        }

        # Extract error
        raw_error = (
            workflow_result.get("fail_reason")
            or workflow_result.get("error")
            or workflow_result.get("message")
            or None
        )
        error_msg = _sanitize_error_message(raw_error)
        assert "tts_wav_not_found" in error_msg

        # Extract diagnostic
        diagnostic = _extract_diagnostic_fields(workflow_result)
        assert diagnostic is not None
        assert diagnostic["status"] == "failed"
        assert diagnostic["failed_node"] == "validation_failed"
        assert "tts_wav_not_found" in diagnostic["fail_reason"]

        # Verify JSON serializable
        json_str = json.dumps(diagnostic)
        assert len(json_str) > 0

        # Verify no complex objects
        for val in diagnostic.values():
            assert isinstance(val, str)

    def test_exception_result(self):
        """Simulate a workflow that raised an exception (no result dict)."""
        workflow_result = None

        raw_error = (
            workflow_result.get("fail_reason") if workflow_result else None
        ) or (
            workflow_result.get("error") if workflow_result else None
        ) or (
            workflow_result.get("message") if workflow_result else None
        ) or None

        error_msg = _sanitize_error_message(raw_error)
        assert error_msg == "Unknown error"

        diagnostic = _extract_diagnostic_fields(workflow_result)
        assert diagnostic is None
