"""
Tests for async task status sync and output_data saving.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime
from src.api.async_task_service import AsyncTaskService
from src.storage.database.batch_models import BatchTaskStatus


class TestAsyncTaskStatusSync:
    """Test async task status synchronization."""

    @pytest.mark.asyncio
    async def test_poll_task_status_completed_saves_output_data(self):
        """Test that completed status saves output_data."""
        # Create mock task
        task = MagicMock()
        task.task_id = "test-task-id"
        task.async_task_id = "test-async-id"
        task.status = BatchTaskStatus.RUNNING
        task.output_data = None
        
        # Create mock async task result
        async_task_result = {
            "status": "completed",
            "result": {
                "final_video_url": "https://example.com/video.mp4",
                "warning": "BGM 混音失败，视频已生成但仅包含 TTS 音频",
                "output_data": {
                    "video_duration": 120.5,
                    "resolution": "1080p",
                    "file_size": 15000000,
                }
            }
        }
        
        # Create service with mock graph_service
        mock_graph_service = MagicMock()
        service = AsyncTaskService(mock_graph_service)
        
        # Manually set runtime to mock (since ASYNC_TASKS_AVAILABLE is False in test env)
        mock_runtime = AsyncMock()
        mock_runtime.get = AsyncMock(return_value=async_task_result)
        service.runtime = mock_runtime
        
        # Create mock db session
        mock_db = AsyncMock()
        
        # Patch ASYNC_TASKS_AVAILABLE to True for this test
        import src.api.async_task_service as ats_module
        original_val = ats_module.ASYNC_TASKS_AVAILABLE
        ats_module.ASYNC_TASKS_AVAILABLE = True
        
        try:
            # Poll status
            await service.poll_task_status(mock_db, task)
        finally:
            ats_module.ASYNC_TASKS_AVAILABLE = original_val
        
        # Verify output_data is saved
        assert task.output_data is not None
        assert task.output_data["output_data"]["video_duration"] == 120.5
        assert task.output_data["output_data"]["resolution"] == "1080p"
        assert task.output_data["output_data"]["file_size"] == 15000000
        
        # Verify other fields
        assert task.status == BatchTaskStatus.SUCCESS
        assert task.final_video_url == "https://example.com/video.mp4"
        assert task.warning == "BGM 混音失败，视频已生成但仅包含 TTS 音频"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_poll_task_status_failed_saves_error(self):
        """Test that failed status saves error message."""
        # Create mock task
        task = MagicMock()
        task.task_id = "test-task-id"
        task.async_task_id = "test-async-id"
        task.status = BatchTaskStatus.RUNNING
        
        # Create mock async task result
        async_task_result = {
            "status": "failed",
            "error": "FFmpeg 返回码=-234, stderr=... [详细错误信息] ..."
        }
        
        # Create service with mock graph_service
        mock_graph_service = MagicMock()
        service = AsyncTaskService(mock_graph_service)
        
        # Manually set runtime to mock (since ASYNC_TASKS_AVAILABLE is False in test env)
        mock_runtime = AsyncMock()
        mock_runtime.get = AsyncMock(return_value=async_task_result)
        service.runtime = mock_runtime
        
        # Create mock db session
        mock_db = AsyncMock()
        
        # Patch ASYNC_TASKS_AVAILABLE to True for this test
        import src.api.async_task_service as ats_module
        original_val = ats_module.ASYNC_TASKS_AVAILABLE
        ats_module.ASYNC_TASKS_AVAILABLE = True
        
        try:
            # Poll status
            await service.poll_task_status(mock_db, task)
        finally:
            ats_module.ASYNC_TASKS_AVAILABLE = original_val
        
        # Verify error is saved
        assert task.status == BatchTaskStatus.FAILED
        assert task.error_message == "FFmpeg 返回码=-234, stderr=... [详细错误信息] ..."
        assert task.error_code == "ASYNC_TASK_FAILED"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_poll_task_status_timeout_saves_timeout_error(self):
        """Test that timeout status saves timeout error."""
        # Create mock task
        task = MagicMock()
        task.task_id = "test-task-id"
        task.async_task_id = "test-async-id"
        task.status = BatchTaskStatus.RUNNING
        
        # Create mock async task result
        async_task_result = {
            "status": "timeout",
            "error": "Task exceeded deadline"
        }
        
        # Create service with mock graph_service
        mock_graph_service = MagicMock()
        service = AsyncTaskService(mock_graph_service)
        
        # Manually set runtime to mock (since ASYNC_TASKS_AVAILABLE is False in test env)
        mock_runtime = AsyncMock()
        mock_runtime.get = AsyncMock(return_value=async_task_result)
        service.runtime = mock_runtime
        
        # Create mock db session
        mock_db = AsyncMock()
        
        # Patch ASYNC_TASKS_AVAILABLE to True for this test
        import src.api.async_task_service as ats_module
        original_val = ats_module.ASYNC_TASKS_AVAILABLE
        ats_module.ASYNC_TASKS_AVAILABLE = True
        
        try:
            # Poll status
            await service.poll_task_status(mock_db, task)
        finally:
            ats_module.ASYNC_TASKS_AVAILABLE = original_val
        
        # Verify timeout error is saved
        assert task.status == BatchTaskStatus.FAILED
        assert task.error_message == "异步任务超过执行期限（1800秒）"
        assert task.error_code == "ASYNC_TASK_TIMEOUT"
        assert task.completed_at is not None

    @pytest.mark.asyncio
    async def test_poll_task_status_pending_maps_to_queued(self):
        """Test that pending status maps to queued."""
        # Create mock task
        task = MagicMock()
        task.task_id = "test-task-id"
        task.async_task_id = "test-async-id"
        task.status = BatchTaskStatus.QUEUED
        
        # Create mock async task result
        async_task_result = {
            "status": "pending"
        }
        
        # Create service with mock graph_service
        mock_graph_service = MagicMock()
        service = AsyncTaskService(mock_graph_service)
        
        # Manually set runtime to mock (since ASYNC_TASKS_AVAILABLE is False in test env)
        mock_runtime = AsyncMock()
        mock_runtime.get = AsyncMock(return_value=async_task_result)
        service.runtime = mock_runtime
        
        # Create mock db session
        mock_db = AsyncMock()
        
        # Patch ASYNC_TASKS_AVAILABLE to True for this test
        import src.api.async_task_service as ats_module
        original_val = ats_module.ASYNC_TASKS_AVAILABLE
        ats_module.ASYNC_TASKS_AVAILABLE = True
        
        try:
            # Poll status
            await service.poll_task_status(mock_db, task)
        finally:
            ats_module.ASYNC_TASKS_AVAILABLE = original_val
        
        # Verify status is still queued
        assert task.status == BatchTaskStatus.QUEUED

    @pytest.mark.asyncio
    async def test_poll_task_status_running_maps_to_running(self):
        """Test that running status maps to running."""
        # Create mock task
        task = MagicMock()
        task.task_id = "test-task-id"
        task.async_task_id = "test-async-id"
        task.status = BatchTaskStatus.QUEUED
        
        # Create mock async task result
        async_task_result = {
            "status": "running"
        }
        
        # Create service with mock graph_service
        mock_graph_service = MagicMock()
        service = AsyncTaskService(mock_graph_service)
        
        # Manually set runtime to mock (since ASYNC_TASKS_AVAILABLE is False in test env)
        mock_runtime = AsyncMock()
        mock_runtime.get = AsyncMock(return_value=async_task_result)
        service.runtime = mock_runtime
        
        # Create mock db session
        mock_db = AsyncMock()
        
        # Patch ASYNC_TASKS_AVAILABLE to True for this test
        import src.api.async_task_service as ats_module
        original_val = ats_module.ASYNC_TASKS_AVAILABLE
        ats_module.ASYNC_TASKS_AVAILABLE = True
        
        try:
            # Poll status
            await service.poll_task_status(mock_db, task)
        finally:
            ats_module.ASYNC_TASKS_AVAILABLE = original_val
        
        # Verify status is running
        assert task.status == BatchTaskStatus.RUNNING


class TestRetryCountIncrement:
    """Test retry_count increment logic."""

    @pytest.mark.asyncio
    async def test_retry_count_increments_not_resets(self):
        """Test that retry_count increments from existing value."""
        from src.api.batch_executor import BatchExecutor
        from src.api.async_task_service import AsyncTaskService
        from src.storage.database.batch_models import BatchJob
        
        # Create mock task with retry_count=3
        task = MagicMock()
        task.task_id = "test-task-id"
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 3
        task.input_data = {"script_text": "test"}
        task.run_id = None
        
        # Create mock batch
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = "test-batch-id"
        batch.concurrency = 2
        batch.tasks = [task]
        
        # Create mock db session
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        
        # Mock the execute result to return the batch
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=batch)
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        # Create mock async task service
        mock_async_service = AsyncMock()
        mock_async_service.submit_task = AsyncMock(return_value={
            "async_task_id": "new-async-id",
            "run_id": "new-run-id",
        })
        
        # Create executor
        mock_graph_service = AsyncMock()
        executor = BatchExecutor(mock_graph_service)
        
        # Retry task
        result = await executor.retry_task(mock_db, batch.batch_id, task.task_id, mock_async_service)
        
        # Verify retry_count is 4 (not 1)
        assert task.retry_count == 4
        # The result comes from async_task_service.submit_task, which returns async_task_id and run_id
        assert result["async_task_id"] == "new-async-id"

    @pytest.mark.asyncio
    async def test_retry_count_starts_at_1_for_first_retry(self):
        """Test that retry_count starts at 1 for first retry."""
        from src.api.batch_executor import BatchExecutor
        from src.storage.database.batch_models import BatchJob
        
        # Create mock task with retry_count=0
        task = MagicMock()
        task.task_id = "test-task-id"
        task.status = BatchTaskStatus.FAILED
        task.retry_count = 0
        task.input_data = {"script_text": "test"}
        task.run_id = None
        
        # Create mock batch
        batch = MagicMock(spec=BatchJob)
        batch.batch_id = "test-batch-id"
        batch.concurrency = 2
        batch.tasks = [task]
        
        # Create mock db session
        mock_db = AsyncMock()
        mock_db.commit = AsyncMock()
        
        # Mock the execute result to return the batch
        mock_result = MagicMock()
        mock_result.scalar_one_or_none = MagicMock(return_value=batch)
        mock_db.execute = AsyncMock(return_value=mock_result)
        
        # Create mock async task service
        mock_async_service = AsyncMock()
        mock_async_service.submit_task = AsyncMock(return_value={
            "async_task_id": "new-async-id",
            "run_id": "new-run-id",
        })
        
        # Create executor
        mock_graph_service = AsyncMock()
        executor = BatchExecutor(mock_graph_service)
        
        # Retry task
        result = await executor.retry_task(mock_db, batch.batch_id, task.task_id, mock_async_service)
        
        # Verify retry_count is 1
        assert task.retry_count == 1
        # The result comes from async_task_service.submit_task, which returns async_task_id and run_id
        assert result["async_task_id"] == "new-async-id"


class TestStatusSyncInBatchTasksEndpoint:
    """Test status sync in batch tasks endpoint."""

    @pytest.mark.asyncio
    async def test_batch_tasks_endpoint_syncs_async_status(self):
        """Test that batch tasks endpoint syncs async status."""
        from src.api.batch_routes import get_batch_tasks
        from src.storage.database.batch_models import BatchTask
        
        # Create mock tasks
        task1 = MagicMock(spec=BatchTask)
        task1.task_id = "task-1"
        task1.async_task_id = "async-1"
        task1.status = BatchTaskStatus.QUEUED
        
        task2 = MagicMock(spec=BatchTask)
        task2.task_id = "task-2"
        task2.async_task_id = "async-2"
        task2.status = BatchTaskStatus.RUNNING
        
        task3 = MagicMock(spec=BatchTask)
        task3.task_id = "task-3"
        task3.async_task_id = None  # No async_task_id
        task3.status = BatchTaskStatus.SUCCESS
        
        # Mock BatchService.get_batch_tasks
        with patch('src.api.batch_routes.BatchService.get_batch') as mock_get_batch, \
             patch('src.api.batch_routes.BatchService.get_batch_tasks') as mock_get_tasks, \
             patch('src.api.async_task_service.AsyncTaskService') as mock_service_class:
            
            # Mock batch
            mock_batch = MagicMock()
            mock_batch.batch_id = "test-batch-id"
            mock_get_batch.return_value = mock_batch
            
            # Mock tasks
            mock_get_tasks.return_value = ([task1, task2, task3], 3)
            
            # Mock async task service
            mock_service = AsyncMock()
            mock_service.poll_task_status = AsyncMock()
            mock_service_class.return_value = mock_service
            
            # Mock db session
            mock_db = AsyncMock()
            
            # Call endpoint with valid UUID format
            import uuid
            valid_batch_id = str(uuid.uuid4())
            result = await get_batch_tasks(valid_batch_id, None, 1, 20, mock_db)
            
            # Verify poll_task_status was called for task1 and task2 (not task3)
            assert mock_service.poll_task_status.call_count == 2
            
            # Verify calls
            calls = [call[0] for call in mock_service.poll_task_status.call_args_list]
            assert any(call[1].task_id == "task-1" for call in calls)
            assert any(call[1].task_id == "task-2" for call in calls)
