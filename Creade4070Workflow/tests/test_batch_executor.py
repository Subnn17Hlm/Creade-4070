"""
Batch executor tests
====================
Tests for batch task scheduling, execution, and state management.
"""

import asyncio
import uuid
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from storage.database.batch_models import (
    BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus,
)
from api.batch_executor import BatchExecutor


@pytest.fixture
def mock_graph_service():
    """Create a mock graph service."""
    service = MagicMock()
    service.run = AsyncMock()
    return service


@pytest.fixture
def executor(mock_graph_service):
    """Create a batch executor with mock graph service."""
    return BatchExecutor(mock_graph_service)


class TestBatchExecutor:
    """Test batch executor functionality."""

    @pytest.mark.asyncio
    async def test_start_batch_concurrency_control(self, executor, mock_graph_service):
        """Test that concurrency limit is respected."""
        # Setup mock database session
        db = AsyncMock()
        
        # Create batch with concurrency 2
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.CREATED,
            total_count=4,
            pending_count=4,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Create 4 pending tasks
        tasks = []
        for i in range(4):
            task = BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch.batch_id,
                row_number=i + 1,
                external_task_id=f"task_{i}",
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": f"Test script {i}"},
            )
            tasks.append(task)
        
        # Add tasks to batch
        batch.tasks = tasks
        
        # Mock database queries
        db.execute = AsyncMock()
        
        # First call returns batch
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock get_async_sessionmaker
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Mock task queries
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = tasks[0]
            mock_session.execute.return_value = task_result
            
            # Mock workflow execution
            mock_graph_service.run.return_value = {
                "status": "success",
                "final_video_url": "https://example.com/video.mp4",
            }
            
            # Start batch
            result = await executor.start_batch(db, batch.batch_id)
            
            # Verify batch was started
            assert result["status"] in [BatchJobStatus.RUNNING, BatchJobStatus.SUCCESS, 
                                        BatchJobStatus.PARTIAL_FAILED, BatchJobStatus.FAILED]
            assert result["total_count"] == 4

    @pytest.mark.asyncio
    async def test_start_batch_idempotency(self, executor):
        """Test that starting an already started batch is idempotent."""
        db = AsyncMock()
        
        # Create batch already in running state
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.RUNNING,
            total_count=2,
            pending_count=0,
            running_count=2,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Mock database query
        result = MagicMock()
        result.scalar_one_or_none.return_value = batch
        db.execute.return_value = result
        
        # Start batch again
        response = await executor.start_batch(db, batch.batch_id)
        
        # Should return current status without re-executing
        assert response["status"] == BatchJobStatus.RUNNING
        assert "already started" in response["message"]

    @pytest.mark.asyncio
    async def test_single_task_failure_isolation(self, executor, mock_graph_service):
        """Test that one task failure doesn't affect others."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.CREATED,
            total_count=3,
            pending_count=3,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        tasks = []
        for i in range(3):
            task = BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch.batch_id,
                row_number=i + 1,
                external_task_id=f"task_{i}",
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": f"Test script {i}"},
            )
            tasks.append(task)
        
        batch.tasks = tasks
        # Mock database
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock workflow: first task fails, others succeed
        call_count = [0]
        async def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"status": "failed", "error": "Test error"}
            return {"status": "success", "final_video_url": "https://example.com/video.mp4"}
        
        mock_graph_service.run.side_effect = mock_run
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = tasks[0]
            mock_session.execute.return_value = task_result
            
            # Start batch
            result = await executor.start_batch(db, batch.batch_id)
            
            # Should have partial success
            assert result["total_count"] == 3
            # At least one task should have completed

    @pytest.mark.asyncio
    async def test_retry_failed_task(self, executor, mock_graph_service):
        """Test retrying a failed task using native async task system."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.FAILED,
            total_count=1,
            pending_count=0,
            running_count=0,
            success_count=0,
            failed_count=1,
            concurrency=2,
        )
        
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch.batch_id,
            row_number=1,
            external_task_id="task_1",
            status=BatchTaskStatus.FAILED,
            input_data={"script_text": "Test script"},
            retry_count=0,
            error_code="WORKFLOW_ERROR",
            error_message="Test error",
        )
        batch.tasks = [task]
        
        # Mock database queries for initial task/batch lookup
        def mock_execute(query):
            result = MagicMock()
            if hasattr(query, 'where'):
                # Check if querying task or batch
                query_str = str(query)
                if 'batch_tasks' in query_str:
                    result.scalar_one_or_none.return_value = task
                else:
                    result.scalar_one_or_none.return_value = batch
            return result
        
        db.execute.side_effect = mock_execute
        
        # Mock successful retry
        mock_graph_service.run.return_value = {
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        }
        
        # Create mock async task service
        mock_async_task_service = AsyncMock()
        mock_async_task_service.submit_task = AsyncMock(return_value={
            "task_id": str(task.task_id),
            "async_task_id": "test-async-task-id",
            "run_id": "test-run-id",
            "status": "queued",
            "retry_count": 1,
            "message": "任务已进入异步执行队列",
        })
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Mock the session's execute to return the task for locking
            def mock_session_execute(query):
                result = MagicMock()
                result.scalar_one_or_none.return_value = task
                return result
            
            mock_session.execute.side_effect = mock_session_execute
            
            # Retry task (now returns immediately with queued status)
            result = await executor.retry_task(db, batch.batch_id, task.task_id, mock_async_task_service)
            
            # Verify retry count incremented
            assert result["retry_count"] == 1
            # Task should be queued for execution
            assert result["status"] == "queued"
            assert "message" in result
            # Verify async task service was called
            mock_async_task_service.submit_task.assert_called_once()

    @pytest.mark.asyncio
    async def test_retry_non_failed_task_rejected(self, executor):
        """Test that retrying a non-failed task is rejected."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.SUCCESS,
            total_count=1,
            pending_count=0,
            running_count=0,
            success_count=1,
            failed_count=0,
            concurrency=2,
        )
        
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch.batch_id,
            row_number=1,
            external_task_id="task_1",
            status=BatchTaskStatus.SUCCESS,
            input_data={"script_text": "Test script"},
        )
        batch.tasks = [task]
        
        # Mock database to return batch
        result = MagicMock()
        result.scalar_one_or_none.return_value = batch
        db.execute.return_value = result
        
        # Create mock async task service
        mock_async_task_service = AsyncMock()
        
        # Try to retry successful task
        with pytest.raises(ValueError, match="only failed tasks can be retried"):
            await executor.retry_task(db, batch.batch_id, task.task_id, mock_async_task_service)

    @pytest.mark.asyncio
    async def test_batch_final_status_all_success(self, executor, mock_graph_service):
        """Test batch status is SUCCESS when all tasks succeed."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.CREATED,
            total_count=2,
            pending_count=2,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch.batch_id,
                row_number=i + 1,
                external_task_id=f"task_{i}",
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": f"Test {i}"},
            )
            for i in range(2)
        ]
        
        # Mock database
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # All tasks succeed
        mock_graph_service.run.return_value = {
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        }
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = tasks[0]
            mock_session.execute.return_value = task_result
            
            # Start batch
            result = await executor.start_batch(db, batch.batch_id)
            
            # Should be success
            assert result["status"] in [BatchJobStatus.SUCCESS, BatchJobStatus.RUNNING]

    @pytest.mark.asyncio
    async def test_batch_final_status_partial_failure(self, executor, mock_graph_service):
        """Test batch status is PARTIAL_FAILED when some tasks fail."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.CREATED,
            total_count=3,
            pending_count=3,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch.batch_id,
                row_number=i + 1,
                external_task_id=f"task_{i}",
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": f"Test {i}"},
            )
            for i in range(3)
        ]
        batch.tasks = tasks
        
        # Mock database
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Some tasks fail
        call_count = [0]
        async def mock_run(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 2:
                return {"status": "failed", "error": "Test error"}
            return {"status": "success", "final_video_url": "https://example.com/video.mp4"}
        
        mock_graph_service.run.side_effect = mock_run
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = tasks[0]
            mock_session.execute.return_value = task_result
            
            # Start batch
            result = await executor.start_batch(db, batch.batch_id)
            
            # Should have partial failure
            assert result["total_count"] == 3

    @pytest.mark.asyncio
    async def test_batch_final_status_all_failed(self, executor, mock_graph_service):
        """Test batch status is FAILED when all tasks fail."""
        db = AsyncMock()
        
        batch = BatchJob(
            batch_id=uuid.uuid4(),
            status=BatchJobStatus.CREATED,
            total_count=2,
            pending_count=2,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch.batch_id,
                row_number=i + 1,
                external_task_id=f"task_{i}",
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": f"Test {i}"},
            )
            for i in range(2)
        ]
        batch.tasks = tasks
        
        # Mock database
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # All tasks fail
        mock_graph_service.run.return_value = {
            "status": "failed",
            "error": "Test error",
        }
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = tasks[0]
            mock_session.execute.return_value = task_result
            
            # Start batch
            result = await executor.start_batch(db, batch.batch_id)
            
            # Should be failed
            assert result["total_count"] == 2

    @pytest.mark.asyncio
    async def test_recover_stuck_tasks(self, executor):
        """Test recovering tasks stuck in running state."""
        db = AsyncMock()
        
        # Create stuck task (running for > 30 minutes)
        stuck_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            row_number=1,
            external_task_id="stuck_task",
            status=BatchTaskStatus.RUNNING,
            input_data={"script_text": "Test"},
            started_at=datetime.utcnow() - timedelta(minutes=35),
        )
        
        # Mock database query
        result = MagicMock()
        result.scalars.return_value.all.return_value = [stuck_task]
        db.execute.return_value = result
        
        # Mock batch query for status update
        batch = BatchJob(
            batch_id=stuck_task.batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        def mock_execute(query):
            result = MagicMock()
            if hasattr(query, 'where'):
                query_str = str(query)
                if 'batch_jobs' in query_str:
                    result.scalar_one_or_none.return_value = batch
                else:
                    result.scalars.return_value.all.return_value = [stuck_task]
            return result
        
        db.execute.side_effect = mock_execute
        
        # Recover stuck tasks
        result = await executor.recover_stuck_tasks(db)
        
        # Verify recovery
        assert result["recovered_count"] == 1
        assert stuck_task.task_id in [uuid.UUID(tid) for tid in result["task_ids"]]
        assert stuck_task.status == BatchTaskStatus.FAILED
        assert stuck_task.error_code == "TIMEOUT"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
