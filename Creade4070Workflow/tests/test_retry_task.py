"""
Tests for retry task functionality.

Covers:
1. Async system available: retry_count increments only after successful submission
2. Async system unavailable: retry_count unchanged, status stays failed
3. Duplicate retry requests don't create duplicate tasks
4. Retry respects concurrency limit
5. Retry returns real errors, not Unknown error
6. Task list and CSV continue HTTP 200
"""

import pytest
import uuid
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import select

from storage.database.batch_models import (
    BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus,
)
from api.batch_executor import BatchExecutor


# ============================================================================
# Fixtures
# ============================================================================

@pytest.fixture
def mock_graph_service():
    """Mock graph service."""
    service = MagicMock()
    service.run = AsyncMock(return_value={"status": "success"})
    return service


@pytest.fixture
def mock_async_task_service_available():
    """Mock async task service that is available and succeeds."""
    service = MagicMock()
    service.runtime = MagicMock()  # Not None = available
    service.submit_task = AsyncMock(return_value={
        "async_task_id": "test-async-task-id",
        "status": "queued",
    })
    return service


@pytest.fixture
def mock_async_task_service_unavailable():
    """Mock async task service that is NOT available."""
    service = MagicMock()
    service.runtime = None  # None = not available
    service.submit_task = AsyncMock(
        side_effect=RuntimeError("Native async task system is not available")
    )
    return service


@pytest.fixture
def mock_db_session():
    """Mock database session."""
    session = AsyncMock()
    result = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    return session


# ============================================================================
# Test 1: Async system available - retry_count increments after successful submission
# ============================================================================

class TestRetrySuccess:
    """Test retry when async system is available."""

    @pytest.mark.asyncio
    async def test_retry_increments_retry_count_after_success(self, mock_graph_service, mock_async_task_service_available):
        """When submission succeeds, retry_count should increment."""
        executor = BatchExecutor(mock_graph_service)
        
        # Create mock batch and task
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.batch_id = batch_id
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 0
        task.input_data = {"title": "test"}
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        batch.concurrency = 2
        
        # Mock db.execute to return the batch
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await executor.retry_task(
            mock_db, batch_id, task_id, 
            async_task_service=mock_async_task_service_available
        )
        
        # Verify retry_count was incremented
        assert task.retry_count == 1
        # Verify status changed to queued
        assert task.status == BatchTaskStatus.QUEUED
        # Verify errors were cleared
        assert task.error_code is None
        assert task.error_message is None
        # Verify async_task_id was set
        assert task.async_task_id == "test-async-task-id"
        # Verify result
        assert result["status"] == "queued"
        assert result["retry_count"] == 1

    @pytest.mark.asyncio
    async def test_retry_clears_old_error_fields(self, mock_graph_service, mock_async_task_service_available):
        """When submission succeeds, old error fields should be cleared."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.batch_id = batch_id
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 2
        task.error_code = "WORKFLOW_ERROR"
        task.error_message = "Previous error"
        task.output_data = {"status": "failed"}
        task.input_data = {"title": "test"}
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        await executor.retry_task(
            mock_db, batch_id, task_id,
            async_task_service=mock_async_task_service_available
        )
        
        # Verify old errors cleared
        assert task.error_code is None
        assert task.error_message is None
        assert task.output_data is None


# ============================================================================
# Test 2: Async system unavailable - retry_count unchanged, status stays failed
# ============================================================================

class TestRetryUnavailable:
    """Test retry when async system is NOT available."""

    @pytest.mark.asyncio
    async def test_retry_unavailable_keeps_retry_count(self, mock_graph_service, mock_async_task_service_unavailable):
        """When async system unavailable, retry_count should NOT increment."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.batch_id = batch_id
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 0
        task.error_code = "WORKFLOW_ERROR"
        task.error_message = "Previous error"
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        # Should raise RuntimeError
        with pytest.raises(RuntimeError, match="Native async task system is not available"):
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_unavailable
            )
        
        # Verify retry_count unchanged
        assert task.retry_count == 0
        # Verify status stays failed
        assert task.status == BatchTaskStatus.FAILED
        # Verify errors preserved
        assert task.error_code == "WORKFLOW_ERROR"
        assert task.error_message == "Previous error"

    @pytest.mark.asyncio
    async def test_retry_unavailable_preserves_error_info(self, mock_graph_service, mock_async_task_service_unavailable):
        """When async system unavailable, original error info should be preserved."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.batch_id = batch_id
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 3
        task.error_code = "EXCEPTION"
        task.error_message = "Connection timeout"
        task.output_data = {"fail_reason": "TTS failed"}
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(RuntimeError):
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_unavailable
            )
        
        # All original state preserved
        assert task.retry_count == 3
        assert task.error_code == "EXCEPTION"
        assert task.error_message == "Connection timeout"
        assert task.output_data == {"fail_reason": "TTS failed"}


# ============================================================================
# Test 3: Duplicate retry requests don't create duplicate tasks
# ============================================================================

class TestRetryIdempotency:
    """Test that duplicate retry requests are handled correctly."""

    @pytest.mark.asyncio
    async def test_retry_non_failed_task_rejected(self, mock_graph_service, mock_async_task_service_available):
        """Retry should only work on failed tasks."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        # Task is already running (from a previous successful retry)
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.status = BatchTaskStatus.RUNNING
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(ValueError, match="only failed tasks can be retried"):
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_available
            )
        
        # submit_task should NOT have been called
        mock_async_task_service_available.submit_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_retry_queued_task_rejected(self, mock_graph_service, mock_async_task_service_available):
        """Retry should reject tasks that are already queued."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.status = BatchTaskStatus.QUEUED
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(ValueError, match="only failed tasks can be retried"):
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_available
            )


# ============================================================================
# Test 4: Retry respects concurrency limit
# ============================================================================

class TestRetryConcurrency:
    """Test that retry respects the concurrency limit."""

    @pytest.mark.asyncio
    async def test_retry_respects_concurrency_limit(self, mock_graph_service, mock_async_task_service_available):
        """Retry should check concurrency limit before submitting."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        # Create a failed task
        failed_task = MagicMock(spec=BatchTask)
        failed_task.task_id = task_id
        failed_task.batch_id = batch_id
        failed_task.status = BatchTaskStatus.FAILED
        failed_task.retry_count = 0
        failed_task.input_data = {"title": "test"}
        
        # Create 2 running tasks (at concurrency limit)
        running_task1 = MagicMock(spec=BatchTask)
        running_task1.task_id = uuid.uuid4()
        running_task1.status = BatchTaskStatus.RUNNING
        
        running_task2 = MagicMock(spec=BatchTask)
        running_task2.task_id = uuid.uuid4()
        running_task2.status = BatchTaskStatus.RUNNING
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [failed_task, running_task1, running_task2]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        # Should raise ValueError about concurrency limit
        with pytest.raises(ValueError, match="Concurrency limit reached"):
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_available
            )
        
        # submit_task should NOT have been called
        mock_async_task_service_available.submit_task.assert_not_called()
        # retry_count should NOT have changed
        assert failed_task.retry_count == 0

    @pytest.mark.asyncio
    async def test_retry_allowed_when_slot_available(self, mock_graph_service, mock_async_task_service_available):
        """Retry should proceed when there's an available slot."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        failed_task = MagicMock(spec=BatchTask)
        failed_task.task_id = task_id
        failed_task.batch_id = batch_id
        failed_task.status = BatchTaskStatus.FAILED
        failed_task.retry_count = 0
        failed_task.input_data = {"title": "test"}
        
        # Only 1 running task (below concurrency limit of 2)
        running_task = MagicMock(spec=BatchTask)
        running_task.task_id = uuid.uuid4()
        running_task.status = BatchTaskStatus.RUNNING
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [failed_task, running_task]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        result = await executor.retry_task(
            mock_db, batch_id, task_id,
            async_task_service=mock_async_task_service_available
        )
        
        # Should succeed
        assert result["status"] == "queued"
        assert failed_task.retry_count == 1


# ============================================================================
# Test 5: Retry returns real errors, not Unknown error
# ============================================================================

class TestRetryErrorMessages:
    """Test that retry returns meaningful error messages."""

    @pytest.mark.asyncio
    async def test_retry_returns_real_error_when_unavailable(self, mock_graph_service, mock_async_task_service_unavailable):
        """When async system unavailable, error should mention the real cause."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 0
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        batch.concurrency = 2
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(RuntimeError) as exc_info:
            await executor.retry_task(
                mock_db, batch_id, task_id,
                async_task_service=mock_async_task_service_unavailable
            )
        
        # Error should mention the real cause
        assert "Native async task system is not available" in str(exc_info.value)
        assert "Unknown error" not in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_retry_returns_real_error_when_no_service(self, mock_graph_service):
        """When no async service provided, error should be clear."""
        executor = BatchExecutor(mock_graph_service)
        
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        
        task = MagicMock(spec=BatchTask)
        task.task_id = task_id
        task.status = BatchTaskStatus.FAILED
        
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = batch_id
        batch.tasks = [task]
        
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = batch
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        with pytest.raises(ValueError, match="async_task_service is required"):
            await executor.retry_task(mock_db, batch_id, task_id, async_task_service=None)


# ============================================================================
# Test 6: Task list and CSV continue HTTP 200
# ============================================================================

class TestTaskListAfterRetry:
    """Test that task list API continues to work after retry attempts."""

    def test_task_list_response_format_unchanged(self):
        """Task list response should not contain input_data/output_data."""
        from api.batch_routes import _serialize_task
        
        task = MagicMock(spec=BatchTask)
        task.task_id = uuid.uuid4()
        task.batch_id = uuid.uuid4()
        task.status = BatchTaskStatus.FAILED
        task.error_code = "WORKFLOW_ERROR"
        task.error_message = "Test error"
        task.retry_count = 1
        task.created_at = datetime.utcnow()
        task.updated_at = datetime.utcnow()
        task.started_at = None
        task.completed_at = None
        task.run_id = None
        task.async_task_id = None
        task.script_text = "Test script"
        task.final_video_url = None
        task.warning = None
        task.input_data = {"title": "test"}  # Should NOT appear in response
        task.output_data = {"fail_reason": "test"}  # Should NOT appear in response
        
        result = _serialize_task(task)
        
        # Should not contain input_data or output_data
        assert "input_data" not in result
        assert "output_data" not in result
        # Should contain error info
        assert result["error_code"] == "WORKFLOW_ERROR"
        assert result["error_message"] == "Test error"
        assert result["retry_count"] == 1
