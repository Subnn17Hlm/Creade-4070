"""
Regression test for start_batch recovery scheduling.

Tests the scenario where:
- Batch table has stale running_count=2
- Real task status: running=0, pending=1
- concurrency=2
- start_batch must select and fallback-submit the pending task
- Task status changes to running
- Repeated calls do not re-submit
"""
import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timedelta, timezone

from api.batch_executor import BatchExecutor, submit_task_to_execution
from storage.database.batch_models import (
    BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus
)


class TestStartBatchRecoveryScheduling:
    """Regression test: batch.status=running, real running=0, pending=1."""

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_recovery_scheduling_with_stale_batch_counts(self, executor, mock_graph_service):
        """
        Regression test for production issue:
        - Batch table has stale running_count=2 (from previous execution)
        - Real task status: running=0, pending=1, success=2
        - concurrency=2
        - start_batch must select and submit the pending task via fallback
        """
        db = AsyncMock()
        
        batch_id = uuid.uuid4()
        
        # Create batch with STALE counts (running_count=2 from old execution)
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,  # Already running from previous execution
            total_count=3,
            pending_count=1,  # Stale count
            running_count=2,  # STALE: shows 2 running but actually 0
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Create real tasks: 2 success, 0 running, 1 pending
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=1,
                status=BatchTaskStatus.SUCCESS,
                input_data={"script_text": "Test 1"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=2,
                status=BatchTaskStatus.SUCCESS,
                input_data={"script_text": "Test 2"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=3,
                external_task_id=None,
                status=BatchTaskStatus.PENDING,  # This is the one that needs to be scheduled
                input_data={"script_text": "Test 3"},
            ),
        ]
        batch.tasks = tasks
        
        # Mock database query to return batch with tasks
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock get_async_sessionmaker for _update_batch_counts_safe
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()  # Use MagicMock instead of AsyncMock
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Mock begin() as async context manager
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            
            # Mock execute as AsyncMock
            mock_session.execute = AsyncMock()
            
            # Mock the count query in _update_batch_counts_safe
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            # Mock claim_task_for_execution to return True (successful claim)
            # and also update the task status to RUNNING
            async def mock_claim_side_effect(task_id, run_id):
                # Find the task and update its status
                for t in tasks:
                    if t.task_id == task_id:
                        t.status = BatchTaskStatus.RUNNING
                        t.run_id = run_id
                        t.started_at = datetime.now(timezone.utc)
                        return True, {}
                return False, {}
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                # Mock ASYNC_TASKS_AVAILABLE to False to force fallback path
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    # Start batch - should recover and submit the pending task
                    result = await executor.start_batch(db, batch_id)
        
        # Verify the result
        assert result["submitted_count"] == 1, f"Expected 1 submitted task, got {result['submitted_count']}"
        assert result["fallback_count"] == 1, f"Expected 1 fallback submission, got {result['fallback_count']}"
        assert result["native_async_count"] == 0, f"Expected 0 native submissions, got {result['native_async_count']}"
        assert result["selected_count"] == 1, f"Expected 1 selected task, got {result['selected_count']}"
        assert result["remaining_count"] == 0, f"Expected 0 remaining, got {result['remaining_count']}"
        
        # Verify the pending task was changed to RUNNING
        pending_task = tasks[2]
        assert pending_task.status == BatchTaskStatus.RUNNING, \
            f"Expected task status RUNNING, got {pending_task.status}"
        assert pending_task.run_id is not None, "Expected run_id to be set"
        assert pending_task.started_at is not None, "Expected started_at to be set"
        
        # Verify response has correct statistics
        assert result["statistics"]["running"] == 1  # 0 + 1 submitted
        assert result["statistics"]["pending"] == 0  # 1 - 1 submitted
        assert result["statistics"]["success"] == 2

    @pytest.mark.asyncio
    async def test_recovery_scheduling_idempotent(self, executor, mock_graph_service):
        """
        Repeated calls to start_batch should not re-submit already running tasks.
        """
        db = AsyncMock()
        
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=3,
            pending_count=0,
            running_count=1,
            success_count=2,
            failed_count=0,
            concurrency=2,
        )
        
        # All tasks: 2 success, 1 running, 0 pending
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=1,
                status=BatchTaskStatus.SUCCESS,
                input_data={"script_text": "Test 1"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=2,
                status=BatchTaskStatus.SUCCESS,
                input_data={"script_text": "Test 2"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=3,
                status=BatchTaskStatus.RUNNING,  # Already running
                input_data={"script_text": "Test 3"},
            ),
        ]
        batch.tasks = tasks
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Second call should not submit any tasks
            result = await executor.start_batch(db, batch_id)
        
        # Should report "already running" since there's 1 running task
        assert result["submitted_count"] == 0
        assert "already" in result["message"].lower() or "running" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_recovery_no_double_submit_on_second_call(self, executor, mock_graph_service):
        """
        After first recovery call submits the task, second call should find 0 pending.
        """
        db = AsyncMock()
        
        batch_id = uuid.uuid4()
        
        # First call: 1 pending task
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=1,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.PENDING,
            input_data={"script_text": "Test 1"},
        )
        batch.tasks = [task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if task.task_id == task_id and task.status == BatchTaskStatus.PENDING:
                task.status = BatchTaskStatus.RUNNING
                task.run_id = run_id
                task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    # First call - should submit the task
                    result1 = await executor.start_batch(db, batch_id)
        
        assert result1["submitted_count"] == 1
        
        # After first call, task should be RUNNING
        assert task.status == BatchTaskStatus.RUNNING
        
        # Second call: now 0 pending, 1 running
        batch_result2 = MagicMock()
        batch_result2.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result2
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            # Second call - should NOT submit again
            result2 = await executor.start_batch(db, batch_id)
        
        assert result2["submitted_count"] == 0
        assert "already" in result2["message"].lower() or "running" in result2["message"].lower()


class TestSubmitTaskToExecutionReturnType:
    """Test that submit_task_to_execution returns (bool, str) tuple."""

    @pytest.mark.asyncio
    async def test_fallback_returns_tuple(self):
        """Fallback path should return (True, 'fallback')."""
        db = AsyncMock()
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            row_number=1,
            status=BatchTaskStatus.PENDING,
            input_data={"script_text": "Test"},
        )
        graph_service = AsyncMock()
        run_id = uuid.uuid4()
        
        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
            with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
                mock_session = AsyncMock()
                mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
                
                result = await submit_task_to_execution(
                    db=db,
                    task=task,
                    graph_service=graph_service,
                    run_id=run_id,
                )
        
        assert isinstance(result, tuple), f"Expected tuple, got {type(result)}"
        assert len(result) == 2, f"Expected 2 elements, got {len(result)}"
        success, method = result
        assert success is True
        assert method == "fallback"

    @pytest.mark.asyncio
    async def test_native_returns_tuple(self):
        """Native path should return (True, 'native')."""
        db = AsyncMock()
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=uuid.uuid4(),
            row_number=1,
            status=BatchTaskStatus.PENDING,
            input_data={"script_text": "Test"},
        )
        
        mock_runtime = MagicMock()
        mock_async_service = AsyncMock()
        mock_async_service.runtime = mock_runtime
        mock_async_service.submit_task = AsyncMock()
        
        graph_service = AsyncMock()
        run_id = uuid.uuid4()
        
        with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', True):
            with patch('api.async_task_service.get_async_task_service', return_value=mock_async_service):
                result = await submit_task_to_execution(
                    db=db,
                    task=task,
                    graph_service=graph_service,
                    run_id=run_id,
                )
        
        assert isinstance(result, tuple)
        success, method = result
        assert success is True
        assert method == "native"


class TestStartBatchResponseFields:
    """Test that start_batch response contains all required fields."""

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_response_contains_all_required_fields(self, executor, mock_graph_service):
        """Response must contain: selected_count, submitted_count, native_async_count, fallback_count, remaining_count."""
        db = AsyncMock()
        
        batch_id = uuid.uuid4()
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.CREATED,
            total_count=1,
            pending_count=1,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.PENDING,
            input_data={"script_text": "Test"},
        )
        batch.tasks = [task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if task.task_id == task_id and task.status == BatchTaskStatus.PENDING:
                task.status = BatchTaskStatus.RUNNING
                task.run_id = run_id
                task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    result = await executor.start_batch(db, batch_id)
        
        # Verify all required fields exist
        assert "selected_count" in result, "Missing selected_count"
        assert "submitted_count" in result, "Missing submitted_count"
        assert "native_async_count" in result, "Missing native_async_count"
        assert "fallback_count" in result, "Missing fallback_count"
        assert "remaining_count" in result, "Missing remaining_count"
        
        # Verify values
        assert result["selected_count"] == 1
        assert result["submitted_count"] == 1
        assert result["native_async_count"] == 0
        assert result["fallback_count"] == 1
        assert result["remaining_count"] == 0


class TestFallbackExecutionSignature:
    """Test that fallback calls _execute_claimed_task with correct parameters."""

    @pytest.mark.asyncio
    async def test_fallback_calls_execute_claimed_task(self):
        """Fallback must call _execute_claimed_task(batch_id, task_id, run_id), not _execute_single_task."""
        from api.batch_executor import submit_task_to_execution
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,  # Already claimed by start_batch
            input_data={"script_text": "Test"},
            run_id=run_id,
        )
        
        mock_graph_service = AsyncMock()
        
        # Track calls to _execute_claimed_task
        calls = []
        
        async def mock_execute(self, batch_id, task_id, run_id):
            calls.append({"batch_id": batch_id, "task_id": task_id, "run_id": run_id})
        
        # Keep patch active while waiting for background task
        with patch.object(BatchExecutor, '_execute_claimed_task', mock_execute):
            with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
                    mock_session = AsyncMock()
                    mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
                    
                    success, method = await submit_task_to_execution(
                        db=db,
                        task=task,
                        graph_service=mock_graph_service,
                        run_id=run_id,
                    )
                    
                    assert success is True
                    assert method == "fallback"
                    
                    # Give the background task a chance to run (while patch is still active)
                    import asyncio
                    await asyncio.sleep(0.3)
        
        # Verify _execute_claimed_task was called with correct parameters
        assert len(calls) == 1, f"Expected 1 call, got {len(calls)}"
        assert calls[0]["batch_id"] == batch_id
        assert calls[0]["task_id"] == task_id
        assert calls[0]["run_id"] == run_id

    @pytest.mark.asyncio
    async def test_fallback_exception_reverts_task_to_pending(self):
        """If background task throws exception, task must be reverted to PENDING."""
        from api.batch_executor import submit_task_to_execution
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            input_data={"script_text": "Test"},
            run_id=run_id,
        )
        
        mock_graph_service = AsyncMock()
        
        async def mock_execute_crash(self, batch_id, task_id, run_id):
            raise RuntimeError("Simulated crash")
        
        # Set up the revert session mock
        # We need begin() to return an async context manager, not a coroutine
        class MockBeginContextManager:
            async def __aenter__(self):
                return None
            async def __aexit__(self, *args):
                return None
        
        mock_revert_session = MagicMock()  # Use MagicMock, not AsyncMock
        mock_revert_session.begin.return_value = MockBeginContextManager()
        
        # execute() is async, so we need it to be an AsyncMock
        task_result = MagicMock()
        task_result.scalar_one_or_none.return_value = task
        mock_revert_session.execute = AsyncMock(return_value=task_result)
        
        # Keep patch active while waiting for background task
        with patch.object(BatchExecutor, '_execute_claimed_task', mock_execute_crash):
            with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                # Patch get_async_sessionmaker in storage.database.db (where it's imported from)
                with patch('storage.database.db.get_async_sessionmaker') as mock_sessionmaker:
                    mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_revert_session
                    
                    success, method = await submit_task_to_execution(
                        db=db,
                        task=task,
                        graph_service=mock_graph_service,
                        run_id=run_id,
                    )
                    
                    assert success is True
                    assert method == "fallback"
                    
                    # Give the background task time to fail and revert (while patch is still active)
                    import asyncio
                    await asyncio.sleep(0.5)
        
        # Verify task was reverted to PENDING
        assert task.status == BatchTaskStatus.PENDING, \
            f"Expected task to be reverted to PENDING, got {task.status}"
        assert task.error_code == "FALLBACK_EXCEPTION"


class TestOrphanTaskRecovery:
    """Test that orphaned RUNNING tasks are detected and recovered."""

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_orphan_running_task_is_recovered(self, executor, mock_graph_service):
        """Task RUNNING for > 30 minutes should be reset to PENDING."""
        from datetime import timedelta
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Create an orphan task: RUNNING for 45 minutes
        orphan_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.utcnow() - timedelta(minutes=45),
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [orphan_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if orphan_task.task_id == task_id and orphan_task.status == BatchTaskStatus.PENDING:
                orphan_task.status = BatchTaskStatus.RUNNING
                orphan_task.run_id = run_id
                orphan_task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    result = await executor.start_batch(db, batch_id)
        
        # The orphan should have been recovered to PENDING, then submitted
        assert result["submitted_count"] == 1, \
            f"Expected orphan to be recovered and submitted, got submitted_count={result['submitted_count']}"
        assert result["fallback_count"] == 1

    @pytest.mark.asyncio
    async def test_recent_running_task_is_not_recovered(self, executor, mock_graph_service):
        """Task RUNNING for < 30 minutes should NOT be reset."""
        from datetime import timedelta
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Create a recent running task: RUNNING for 5 minutes
        recent_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.utcnow() - timedelta(minutes=5),
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [recent_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Should return "already running" since there's 1 running task
            result = await executor.start_batch(db, batch_id)
        
        # Should NOT submit because there's already a running task
        assert result["submitted_count"] == 0
        assert "already" in result["message"].lower() or "running" in result["message"].lower()
        
        # Task should still be RUNNING
        assert recent_task.status == BatchTaskStatus.RUNNING


class TestExecuteClaimedTask:
    """Test _execute_claimed_task method directly."""

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_execute_claimed_task_runs_workflow(self, executor, mock_graph_service):
        """_execute_claimed_task should run the workflow and update status."""
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            run_id=run_id,
            started_at=datetime.utcnow(),
            input_data={"script_text": "Test script"},
        )
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Mock fetch query
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = task
            mock_session.execute.return_value = task_result
            
            # Mock context manager for begin()
            mock_cm = AsyncMock()
            mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
            mock_cm.__aexit__ = AsyncMock(return_value=None)
            mock_session.begin.return_value = mock_cm
            
            with patch('coze_coding_utils.runtime_ctx.context.new_context') as mock_ctx:
                mock_ctx_instance = MagicMock()
                mock_ctx.return_value = mock_ctx_instance
                
                await executor._execute_claimed_task(
                    batch_id=batch_id,
                    task_id=task_id,
                    run_id=run_id,
                )
        
        # Verify workflow was called with correct input
        mock_graph_service.run.assert_called_once()
        call_args = mock_graph_service.run.call_args
        workflow_input = call_args[0][0]
        assert workflow_input["script_text"] == "Test script"
        assert workflow_input["run_id"] == str(run_id)
        assert workflow_input["script_source"] == "manual"

    @pytest.mark.asyncio
    async def test_execute_claimed_task_skips_non_running(self, executor, mock_graph_service):
        """_execute_claimed_task should skip if task is no longer RUNNING."""
        batch_id = uuid.uuid4()
        task_id = uuid.uuid4()
        run_id = uuid.uuid4()
        
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.SUCCESS,  # Already completed
            input_data={"script_text": "Test"},
        )
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = task
            mock_session.execute.return_value = task_result
            
            await executor._execute_claimed_task(
                batch_id=batch_id,
                task_id=task_id,
                run_id=run_id,
            )
        
        # Workflow should NOT have been called
        mock_graph_service.run.assert_not_called()


class TestAutoBackfillAfterCompletion:
    """
    Regression test for production issue:
    并发2、共6条，前2条完成后自动补位 → running=2、pending=2。

    Verifies that _trigger_next_pending_task correctly fills available
    concurrency slots after tasks complete, and that datetime errors
    in orphan detection do not block the auto-backfill.
    """

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_auto_backfill_concurrency2_total6(self, executor, mock_graph_service):
        """
        Scenario: concurrency=2, total=6 tasks.
        - Tasks 1,2 are SUCCESS (completed).
        - Tasks 3,4 should be auto-backfilled to RUNNING.
        - Tasks 5,6 remain PENDING.
        - Final: running=2, pending=2, success=2.

        Also verifies that orphan detection with mixed naive/aware datetimes
        does not raise TypeError or block scheduling.
        """
        batch_id = uuid.uuid4()

        # Create batch with concurrency=2
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=6,
            pending_count=4,
            running_count=0,
            success_count=2,
            failed_count=0,
            concurrency=2,
        )

        # Create 6 tasks: 2 SUCCESS, 4 PENDING
        tasks = []
        for i in range(6):
            task = BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=i + 1,
                input_data={"script_text": f"Test {i+1}"},
            )
            if i < 2:
                task.status = BatchTaskStatus.SUCCESS
                task.completed_at = datetime.now(timezone.utc)
            else:
                task.status = BatchTaskStatus.PENDING
            tasks.append(task)

        batch.tasks = tasks

        # Track claimed tasks
        claimed_task_ids = []

        async def mock_claim_side_effect(task_id, run_id):
            for t in tasks:
                if t.task_id == task_id and t.status == BatchTaskStatus.PENDING:
                    t.status = BatchTaskStatus.RUNNING
                    t.run_id = run_id
                    t.started_at = datetime.utcnow()  # naive datetime (simulates DB)
                    claimed_task_ids.append(task_id)
                    return True, {}
            return False, {}

        def _create_mock_session():
            """Each _trigger_next_pending_task call creates a new session."""
            mock_session = MagicMock()

            batch_q_result = MagicMock()
            batch_q_result.scalar_one_or_none.return_value = batch

            count_q_result = MagicMock()
            # Dynamically count RUNNING tasks
            count_q_result.scalar.return_value = sum(
                1 for t in tasks if t.status in (BatchTaskStatus.RUNNING, BatchTaskStatus.QUEUED)
            )

            call_num = [0]
            def execute_side_effect(*args, **kwargs):
                call_num[0] += 1
                n = call_num[0]
                if n % 3 == 1:
                    return batch_q_result
                elif n % 3 == 2:
                    # Re-count running each time
                    count_q_result.scalar.return_value = sum(
                        1 for t in tasks if t.status in (BatchTaskStatus.RUNNING, BatchTaskStatus.QUEUED)
                    )
                    return count_q_result
                else:
                    # Return next PENDING task
                    for t in tasks:
                        if t.status == BatchTaskStatus.PENDING:
                            r = MagicMock()
                            r.scalar_one_or_none.return_value = t
                            return r
                    r = MagicMock()
                    r.scalar_one_or_none.return_value = None
                    return r

            mock_session.execute = AsyncMock(side_effect=execute_side_effect)

            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            return mock_session

        # Each call to get_async_sessionmaker()() should return a fresh session
        session_factory = MagicMock()
        session_factory.return_value.__aenter__ = AsyncMock(side_effect=lambda: _create_mock_session())
        session_factory.return_value.__aexit__ = AsyncMock(return_value=False)

        with patch('api.batch_executor.get_async_sessionmaker', return_value=session_factory):
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect

                with patch('api.batch_executor.submit_task_to_execution', new_callable=AsyncMock) as mock_submit:
                    mock_submit.return_value = (True, "fallback")

                    with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                        await executor._trigger_next_pending_task(batch_id)
                        await executor._trigger_next_pending_task(batch_id)

        # Verify: 2 tasks were claimed and set to RUNNING
        assert len(claimed_task_ids) == 2, f"Expected 2 claimed tasks, got {len(claimed_task_ids)}"

        # Count final states
        running_tasks = [t for t in tasks if t.status == BatchTaskStatus.RUNNING]
        pending_tasks = [t for t in tasks if t.status == BatchTaskStatus.PENDING]
        success_tasks = [t for t in tasks if t.status == BatchTaskStatus.SUCCESS]

        assert len(running_tasks) == 2, f"Expected 2 RUNNING, got {len(running_tasks)}"
        assert len(pending_tasks) == 2, f"Expected 2 PENDING, got {len(pending_tasks)}"
        assert len(success_tasks) == 2, f"Expected 2 SUCCESS, got {len(success_tasks)}"

    @pytest.mark.asyncio
    async def test_orphan_detection_naive_aware_mixed(self, executor, mock_graph_service):
        """
        Verify that orphan detection handles mixed naive/aware datetimes
        without raising TypeError, and does not block start_batch.
        """
        db = AsyncMock()
        batch_id = uuid.uuid4()

        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=4,
            pending_count=2,
            running_count=2,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )

        # Create tasks with mixed datetime types (simulating DB inconsistency)
        tasks = [
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=1,
                status=BatchTaskStatus.RUNNING,
                started_at=datetime.utcnow(),  # NAIVE datetime (from DB)
                input_data={"script_text": "Test 1"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=2,
                status=BatchTaskStatus.RUNNING,
                started_at=datetime.now(timezone.utc),  # AWARE datetime
                input_data={"script_text": "Test 2"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=3,
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": "Test 3"},
            ),
            BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=4,
                status=BatchTaskStatus.PENDING,
                input_data={"script_text": "Test 4"},
            ),
        ]
        batch.tasks = tasks

        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result

        # start_batch should NOT raise TypeError even with mixed datetimes
        # Since running_count > 0, it will return early with "already running" message
        # But the orphan detection should not crash
        result = await executor.start_batch(db, batch_id)

        # The method should complete without error
        assert "batch_id" in result
        assert "statistics" in result
        # Running tasks should still be counted
        assert result["statistics"]["running"] == 2
        assert result["statistics"]["pending"] == 2


class TestDatetimeTimezoneHandling:
    """Test that datetime comparisons handle naive/aware correctly."""

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    def test_ensure_utc_aware_none(self):
        """None input returns None."""
        from api.batch_executor import ensure_utc_aware
        assert ensure_utc_aware(None) is None

    def test_ensure_utc_aware_naive(self):
        """Naive datetime gets UTC tzinfo attached."""
        from api.batch_executor import ensure_utc_aware
        from datetime import timezone
        
        naive_dt = datetime(2026, 1, 1, 12, 0, 0)
        result = ensure_utc_aware(naive_dt)
        
        assert result.tzinfo is not None
        assert result.tzinfo == timezone.utc
        assert result.year == 2026
        assert result.hour == 12

    def test_ensure_utc_aware_already_aware(self):
        """Aware datetime is converted to UTC."""
        from api.batch_executor import ensure_utc_aware
        from datetime import timezone, timedelta
        
        # Create a datetime in UTC+5
        tz_plus5 = timezone(timedelta(hours=5))
        aware_dt = datetime(2026, 1, 1, 17, 0, 0, tzinfo=tz_plus5)  # 17:00+05 = 12:00 UTC
        result = ensure_utc_aware(aware_dt)
        
        assert result.tzinfo == timezone.utc
        assert result.hour == 12  # Converted to UTC

    def test_utc_now_is_aware(self):
        """utc_now() returns timezone-aware datetime."""
        from api.batch_executor import utc_now
        
        now = utc_now()
        assert now.tzinfo is not None

    @pytest.mark.asyncio
    async def test_orphan_recovery_with_naive_started_at(self, executor, mock_graph_service):
        """Orphan recovery works when started_at is naive UTC (from database)."""
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Naive datetime (as returned by most databases)
        orphan_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.utcnow() - timedelta(minutes=45),  # naive, 45 min ago
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [orphan_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if orphan_task.task_id == task_id and orphan_task.status == BatchTaskStatus.PENDING:
                orphan_task.status = BatchTaskStatus.RUNNING
                orphan_task.run_id = run_id
                orphan_task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    # Should NOT raise "can't subtract offset-naive and offset-aware"
                    result = await executor.start_batch(db, batch_id)
        
        # Orphan should have been recovered
        assert result["submitted_count"] == 1

    @pytest.mark.asyncio
    async def test_orphan_recovery_with_aware_started_at(self, executor, mock_graph_service):
        """Orphan recovery works when started_at is timezone-aware UTC."""
        from datetime import timezone
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Aware datetime in UTC
        orphan_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=45),  # aware UTC, 45 min ago
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [orphan_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if orphan_task.task_id == task_id and orphan_task.status == BatchTaskStatus.PENDING:
                orphan_task.status = BatchTaskStatus.RUNNING
                orphan_task.run_id = run_id
                orphan_task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    result = await executor.start_batch(db, batch_id)
        
        assert result["submitted_count"] == 1

    @pytest.mark.asyncio
    async def test_orphan_recovery_with_different_utc_offset(self, executor, mock_graph_service):
        """Orphan recovery works with different UTC offsets."""
        from datetime import timezone
        
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # Aware datetime in UTC+8 (e.g., China Standard Time)
        tz_cst = timezone(timedelta(hours=8))
        orphan_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.now(tz_cst) - timedelta(minutes=45),  # aware UTC+8, 45 min ago
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [orphan_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Mock claim_task_for_execution to update task status
        async def mock_claim_side_effect(task_id, run_id):
            if orphan_task.task_id == task_id and orphan_task.status == BatchTaskStatus.PENDING:
                orphan_task.status = BatchTaskStatus.RUNNING
                orphan_task.run_id = run_id
                orphan_task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    result = await executor.start_batch(db, batch_id)
        
        assert result["submitted_count"] == 1

    @pytest.mark.asyncio
    async def test_recent_task_not_recovered(self, executor, mock_graph_service):
        """Task running for < 30 minutes is NOT recovered."""
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        recent_task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=datetime.utcnow() - timedelta(minutes=5),  # naive, only 5 min ago
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [recent_task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            result = await executor.start_batch(db, batch_id)
        
        # Should NOT submit - task is still legitimately running
        assert result["submitted_count"] == 0

    @pytest.mark.asyncio
    async def test_started_at_none_is_safe(self, executor, mock_graph_service):
        """Task with started_at=None does not crash."""
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.RUNNING,
            total_count=1,
            pending_count=0,
            running_count=1,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        # RUNNING but started_at is None (edge case)
        task_no_started = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            started_at=None,  # No started_at
            run_id=uuid.uuid4(),
            input_data={"script_text": "Test"},
        )
        batch.tasks = [task_no_started]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = AsyncMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            # Should NOT raise any exception
            result = await executor.start_batch(db, batch_id)
        
        # Task with started_at=None should not be recovered (condition: t.started_at is falsy)
        # So it stays RUNNING and blocks submission
        assert result["submitted_count"] == 0


class TestConcurrencySafety:
    """
    Tests for concurrent execution safety:
    - Two concurrent start_batch calls should not double-execute the same task
    - Recovery and refill concurrent calls should not double-execute
    - Old attempt callbacks should not overwrite new state
    """

    @pytest.fixture
    def mock_graph_service(self):
        service = AsyncMock()
        service.run = AsyncMock(return_value={
            "status": "success",
            "final_video_url": "https://example.com/video.mp4",
        })
        return service

    @pytest.fixture
    def executor(self, mock_graph_service):
        return BatchExecutor(mock_graph_service)

    @pytest.mark.asyncio
    async def test_concurrent_start_batch_only_claims_once(self, executor, mock_graph_service):
        """
        Two concurrent start_batch calls should only claim the task once.
        The second call should find the task already RUNNING and skip it.
        """
        db = AsyncMock()
        batch_id = uuid.uuid4()
        
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.CREATED,
            total_count=1,
            pending_count=1,
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=2,
        )
        
        task = BatchTask(
            task_id=uuid.uuid4(),
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.PENDING,
            input_data={"script_text": "Test"},
        )
        batch.tasks = [task]
        
        batch_result = MagicMock()
        batch_result.scalar_one_or_none.return_value = batch
        db.execute.return_value = batch_result
        
        # Track claim calls
        claim_calls = []
        
        async def mock_claim_side_effect(task_id, run_id):
            claim_calls.append((task_id, run_id))
            # First call succeeds, subsequent calls fail (simulating atomic claim)
            if task.status == BatchTaskStatus.PENDING:
                task.status = BatchTaskStatus.RUNNING
                task.run_id = run_id
                task.started_at = datetime.now(timezone.utc)
                return True, {}
            return False, {}
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock()
            
            count_result = MagicMock()
            count_result.scalar.return_value = 0
            mock_session.execute.return_value = count_result
            
            with patch('api.batch_executor.claim_task_for_execution', new_callable=AsyncMock) as mock_claim:
                mock_claim.side_effect = mock_claim_side_effect
                
                with patch('api.async_task_service.ASYNC_TASKS_AVAILABLE', False):
                    # First call - should claim and submit
                    result1 = await executor.start_batch(db, batch_id)
                    
                    # Second call - should find task already RUNNING
                    result2 = await executor.start_batch(db, batch_id)
        
        # First call should have submitted
        assert result1["submitted_count"] == 1
        # Second call should NOT have submitted (task already RUNNING)
        assert result2["submitted_count"] == 0
        # claim_task_for_execution should have been called only once
        # (second call returns early because running_count > 0)
        assert len(claim_calls) == 1

    @pytest.mark.asyncio
    async def test_run_id_lease_prevents_old_callback(self, executor, mock_graph_service):
        """
        Old attempt callback with mismatched run_id should not overwrite new state.
        """
        task_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        old_run_id = uuid.uuid4()
        new_run_id = uuid.uuid4()
        
        db = AsyncMock()
        
        # Task is now running with new_run_id (new attempt)
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            run_id=new_run_id,  # New run_id
            input_data={"script_text": "Test"},
        )
        
        task_result = MagicMock()
        task_result.scalar_one_or_none.return_value = task
        db.execute.return_value = task_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock(return_value=task_result)
            
            # Old attempt tries to update status with old_run_id
            await executor._update_task_final_status(
                task_id=task_id,
                batch_id=batch_id,
                success=True,
                result={"status": "success", "final_video_url": "https://old.com/video.mp4"},
                error=None,
                run_id=old_run_id,  # Old run_id - should be rejected
            )
        
        # Task should NOT have been updated (still has new_run_id)
        assert task.run_id == new_run_id
        # Task status should NOT have changed to SUCCESS
        assert task.status == BatchTaskStatus.RUNNING

    @pytest.mark.asyncio
    async def test_mark_failed_with_wrong_run_id_rejected(self, executor, mock_graph_service):
        """
        _mark_task_failed with wrong run_id should not mark the task as failed.
        """
        task_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        old_run_id = uuid.uuid4()
        new_run_id = uuid.uuid4()
        
        db = AsyncMock()
        
        # Task is now running with new_run_id
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            run_id=new_run_id,
            input_data={"script_text": "Test"},
        )
        
        task_result = MagicMock()
        task_result.scalar_one_or_none.return_value = task
        db.execute.return_value = task_result
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            class MockBeginContextManager:
                async def __aenter__(self):
                    return None
                async def __aexit__(self, *args):
                    pass
            mock_session.begin.return_value = MockBeginContextManager()
            mock_session.execute = AsyncMock(return_value=task_result)
            
            # Old attempt tries to mark as failed with old_run_id
            await executor._mark_task_failed(
                task_id=task_id,
                batch_id=batch_id,
                error_code="OLD_ATTEMPT_ERROR",
                error_message="Old attempt failed",
                run_id=old_run_id,  # Wrong run_id
            )
        
        # Task should NOT have been marked as failed
        assert task.status == BatchTaskStatus.RUNNING
        assert task.run_id == new_run_id

    @pytest.mark.asyncio
    async def test_execute_claimed_task_verifies_run_id(self, executor, mock_graph_service):
        """
        _execute_claimed_task should verify run_id before executing.
        """
        task_id = uuid.uuid4()
        batch_id = uuid.uuid4()
        correct_run_id = uuid.uuid4()
        wrong_run_id = uuid.uuid4()
        
        # Task is RUNNING with correct_run_id
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=1,
            status=BatchTaskStatus.RUNNING,
            run_id=correct_run_id,
            input_data={"script_text": "Test"},
        )
        
        with patch('api.batch_executor.get_async_sessionmaker') as mock_sessionmaker:
            mock_session = MagicMock()
            mock_sessionmaker.return_value.return_value.__aenter__.return_value = mock_session
            
            task_result = MagicMock()
            task_result.scalar_one_or_none.return_value = task
            mock_session.execute = AsyncMock(return_value=task_result)
            
            # Try to execute with wrong run_id
            await executor._execute_claimed_task(
                batch_id=batch_id,
                task_id=task_id,
                run_id=wrong_run_id,  # Wrong run_id
            )
        
        # Workflow should NOT have been called
        mock_graph_service.run.assert_not_called()
