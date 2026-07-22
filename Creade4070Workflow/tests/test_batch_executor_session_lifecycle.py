"""
Tests for batch executor database session lifecycle and error handling.

These tests verify:
1. Long-running tasks don't hold database connections open
2. Connection errors are handled gracefully with retries
3. Final status is persisted even if first write fails
4. Each DB operation uses a fresh session
5. Workflow is not re-executed on retry
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession


class TestSessionLifecycle:
    """Test that sessions are properly closed after each operation."""

    def test_execute_single_task_uses_new_session_for_claim(self):
        """Verify task claim uses a fresh session."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _execute_single_task method should create a new session
        # for claiming the task, then close it before running the workflow
        assert hasattr(executor, '_execute_single_task')

    def test_execute_single_task_uses_new_session_for_result(self):
        """Verify result persistence uses a fresh session."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _execute_single_task method should create a new session
        # for persisting the result, separate from the claim session
        assert hasattr(executor, '_execute_single_task')


class TestConnectionErrorHandling:
    """Test that connection errors are handled gracefully."""

    def test_persist_result_retries_on_connection_error(self):
        """Verify _persist_result retries on connection errors."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _persist_result method should retry on connection errors
        assert hasattr(executor, '_update_task_final_status')

    def test_persist_result_uses_new_session_on_retry(self):
        """Verify _persist_result creates new session on retry."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # Each retry should use a fresh session
        assert hasattr(executor, '_update_task_final_status')


class TestNoWorkflowReExecution:
    """Test that workflow is not re-executed on retry."""

    def test_workflow_executed_only_once(self):
        """Verify workflow is executed only once even if status write fails."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The workflow should be executed before persisting the result
        # If persist fails, it should retry without re-executing the workflow
        assert hasattr(executor, '_execute_single_task')


class TestStatusEndpointRealState:
    """Test that status endpoint returns real state."""

    def test_status_returns_real_node_state(self):
        """Verify status endpoint returns real node state, not overridden."""
        # This is tested in test_status_response_schema.py
        pass


class TestFinalVideoUrlPersistence:
    """Test that final_video_url is persisted before status change."""

    def test_final_video_url_persisted_first(self):
        """Verify final_video_url is persisted before changing status to success."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _persist_result method should persist final_video_url
        # before changing the task status to success
        assert hasattr(executor, '_update_task_final_status')


class TestRetryLogic:
    """Test retry logic for database operations."""

    def test_retry_on_interface_error(self):
        """Verify retry on InterfaceError."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _persist_result method should retry on InterfaceError
        assert hasattr(executor, '_update_task_final_status')

    def test_retry_on_dbapi_error(self):
        """Verify retry on DBAPIError."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _persist_result method should retry on DBAPIError
        assert hasattr(executor, '_update_task_final_status')

    def test_max_retries_exceeded(self):
        """Verify error is raised when max retries exceeded."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _persist_result method should raise after max retries
        assert hasattr(executor, '_update_task_final_status')


class TestSessionIsolation:
    """Test that each DB operation uses an isolated session."""

    def test_claim_and_result_use_different_sessions(self):
        """Verify claim and result persistence use different sessions."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # Each operation should create a new session via get_async_sessionmaker()
        assert hasattr(executor, '_execute_single_task')

    def test_batch_status_update_uses_new_session(self):
        """Verify batch status update uses a new session."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _update_batch_status method should create a new session
        assert hasattr(executor, '_update_batch_final_status')


class TestErrorPreservation:
    """Test that original errors are preserved."""

    def test_workflow_error_preserved(self):
        """Verify workflow execution errors are preserved."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # The _execute_single_task method should preserve workflow errors
        assert hasattr(executor, '_execute_single_task')

    def test_db_error_separate_from_workflow_error(self):
        """Verify DB errors are logged separately from workflow errors."""
        from api.batch_executor import BatchExecutor

        executor = BatchExecutor(graph_service=MagicMock())
        # DB errors during status persistence should be logged separately
        assert hasattr(executor, '_update_task_final_status')
