"""
Tests for retry task unified scheduling path.

These tests verify that:
1. Retry uses the same scheduling path as start_batch
2. When native async is unavailable, fallback to PENDING status
3. State changes only happen after successful scheduling
4. Concurrency limits are respected
"""
import pytest
import uuid
import asyncio
from datetime import datetime
from unittest.mock import MagicMock, AsyncMock, patch

from api.batch_executor import BatchExecutor
from storage.database.batch_models import BatchTask, BatchJob, BatchTaskStatus, BatchJobStatus


def _make_task(status=BatchTaskStatus.FAILED, retry_count=0, run_id=None):
    task = BatchTask()
    task.task_id = uuid.uuid4()
    task.status = status
    task.retry_count = retry_count
    task.run_id = run_id
    task.error_code = "WORKFLOW_ERROR" if status == BatchTaskStatus.FAILED else None
    task.error_message = "Unknown error" if status == BatchTaskStatus.FAILED else None
    task.created_at = datetime.utcnow()
    task.updated_at = datetime.utcnow()
    return task


def _make_batch(tasks=None, concurrency=2):
    batch = BatchJob()
    batch.batch_id = uuid.uuid4()
    batch.status = BatchJobStatus.RUNNING
    batch.concurrency = concurrency
    batch.created_at = datetime.utcnow()
    batch.updated_at = datetime.utcnow()
    batch.tasks = tasks or []
    return batch


class TestRetryResetsToPending:
    """Test that retry resets task to PENDING when using fallback."""

    @pytest.mark.asyncio
    async def test_retry_sets_status_to_pending_when_native_unavailable(self):
        """After successful retry with native async unavailable, task status should be PENDING."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        batch = _make_batch(tasks=[task])

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
            with patch('api.batch_executor.asyncio.create_task') as mock_create_task:
                mock_create_task.return_value = MagicMock()
                result = await executor.retry_task(db, batch.batch_id, task.task_id)

                assert result["status"] == "pending"
                assert task.status == BatchTaskStatus.PENDING

    @pytest.mark.asyncio
    async def test_retry_increments_retry_count(self):
        """Retry should increment retry_count after successful scheduling."""
        task = _make_task(status=BatchTaskStatus.FAILED, retry_count=2)
        batch = _make_batch(tasks=[task])

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
            with patch('api.batch_executor.asyncio.create_task') as mock_create_task:
                mock_create_task.return_value = MagicMock()
                result = await executor.retry_task(db, batch.batch_id, task.task_id)

                assert task.retry_count == 3
                assert result["retry_count"] == 3


class TestRetryIdempotency:
    """Test that duplicate retry requests don't cause issues."""

    @pytest.mark.asyncio
    async def test_retry_non_failed_task_rejected(self):
        """Retry should reject tasks that are not in FAILED status."""
        task = _make_task(status=BatchTaskStatus.SUCCESS)
        batch = _make_batch(tasks=[task])

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError) as exc_info:
            await executor.retry_task(db, batch.batch_id, task.task_id)

        assert "only failed tasks can be retried" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_retry_running_task_rejected(self):
        """Retry should reject tasks that are currently running."""
        task = _make_task(status=BatchTaskStatus.RUNNING)
        batch = _make_batch(tasks=[task])

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError) as exc_info:
            await executor.retry_task(db, batch.batch_id, task.task_id)

        assert "only failed tasks can be retried" in str(exc_info.value).lower()


class TestRetryConcurrency:
    """Test that retry respects concurrency limits."""

    @pytest.mark.asyncio
    async def test_retry_allowed_when_running_below_limit(self):
        """Retry should be allowed when running tasks < concurrency limit."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        running_task = _make_task(status=BatchTaskStatus.RUNNING)
        batch = _make_batch(tasks=[task, running_task], concurrency=2)

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
            with patch('api.batch_executor.asyncio.create_task') as mock_create_task:
                mock_create_task.return_value = MagicMock()
                result = await executor.retry_task(db, batch.batch_id, task.task_id)

                # Should succeed because running (1) < concurrency (2)
                assert result["status"] == "pending"

    @pytest.mark.asyncio
    async def test_retry_blocked_when_at_concurrency_limit(self):
        """Retry should be blocked when running tasks >= concurrency limit."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        running_task1 = _make_task(status=BatchTaskStatus.RUNNING)
        running_task2 = _make_task(status=BatchTaskStatus.RUNNING)
        batch = _make_batch(tasks=[task, running_task1, running_task2], concurrency=2)

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError) as exc_info:
            await executor.retry_task(db, batch.batch_id, task.task_id)

        assert "concurrency limit" in str(exc_info.value).lower()


class TestRetryErrorMessages:
    """Test that retry returns real error messages."""

    @pytest.mark.asyncio
    async def test_retry_returns_real_error_when_scheduling_fails(self):
        """Retry should return the actual error message from scheduling."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        running_task1 = _make_task(status=BatchTaskStatus.RUNNING)
        running_task2 = _make_task(status=BatchTaskStatus.RUNNING)
        batch = _make_batch(tasks=[task, running_task1, running_task2], concurrency=2)

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError) as exc_info:
            await executor.retry_task(db, batch.batch_id, task.task_id)

        # Should have a real error message, not "Unknown error"
        assert str(exc_info.value) != "Unknown error"
        assert "concurrency limit" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_retry_does_not_return_unknown_error(self):
        """Retry should not return 'Unknown error' for known failure cases."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        running_task1 = _make_task(status=BatchTaskStatus.RUNNING)
        running_task2 = _make_task(status=BatchTaskStatus.RUNNING)
        batch = _make_batch(tasks=[task, running_task1, running_task2], concurrency=2)

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(ValueError) as exc_info:
            await executor.retry_task(db, batch.batch_id, task.task_id)

        assert str(exc_info.value) != "Unknown error"
        assert "concurrency limit" in str(exc_info.value).lower()


class TestFallbackExecution:
    """Test that fallback execution uses asyncio.create_task."""

    @pytest.mark.asyncio
    async def test_fallback_uses_asyncio_create_task(self):
        """When native async is unavailable, fallback should use asyncio.create_task."""
        task = _make_task(status=BatchTaskStatus.FAILED)
        batch = _make_batch(tasks=[task])

        executor = BatchExecutor(MagicMock())

        db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        db.execute = AsyncMock(return_value=mock_result)
        db.commit = AsyncMock()

        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
            with patch('api.batch_executor.asyncio.create_task') as mock_create_task:
                mock_create_task.return_value = MagicMock()
                await executor.retry_task(db, batch.batch_id, task.task_id)

                mock_create_task.assert_called_once()


class TestTaskListResponseFormat:
    """Test that task list response format is unchanged after retry."""

    @pytest.mark.asyncio
    async def test_task_list_response_format_unchanged(self):
        """Task list response should not contain input_data or output_data after retry."""
        from api.batch_routes import _serialize_task

        task = _make_task(status=BatchTaskStatus.PENDING)
        task.script_id = uuid.uuid4()
        task.title = "Test Script"
        task.script_text = "Test script text"
        task.final_video_url = None
        task.warning = None
        task.async_task_id = None
        task.started_at = None
        task.completed_at = None

        result = _serialize_task(task)

        assert "input_data" not in result
        assert "output_data" not in result
        assert result["status"] == "pending"
