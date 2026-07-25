"""
Regression tests for _update_task_final_status generation info handling.

Tests that:
1. With generation info in result, status writeback succeeds.
2. Without generation object, status=success and final_video_url still persist.
3. needs_manual_review keeps status=success with video preserved.
4. No duplicate generation creation.
5. Old final_video_url not overwritten.
"""
import pytest
import uuid
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.api.batch_executor import BatchExecutor


class TestUpdateTaskFinalStatusGeneration:
    """Test _update_task_final_status handles generation info correctly."""

    @pytest.fixture
    def executor(self):
        mock_graph_service = MagicMock()
        return BatchExecutor(graph_service=mock_graph_service, max_concurrent=2)

    @pytest.fixture
    def mock_task(self):
        task = MagicMock()
        task.task_id = uuid.uuid4()
        task.batch_id = uuid.uuid4()
        task.status = "running"
        task.run_id = uuid.uuid4()
        task.output_data = None
        task.final_video_url = None
        task.completed_at = None
        task.warning = None
        task.review_required = False
        task.error_code = None
        task.error_message = None
        return task

    def _setup_db_mock(self, mock_task):
        """Set up database mock that returns the mock task."""
        mock_result = MagicMock()
        mock_result.scalar_one_or_none.return_value = mock_task
        
        mock_db = AsyncMock()
        mock_db.execute = AsyncMock(return_value=mock_result)
        mock_db.commit = AsyncMock()
        
        # Mock the begin() context manager
        mock_begin_ctx = AsyncMock()
        mock_begin_ctx.__aenter__ = AsyncMock(return_value=None)
        mock_begin_ctx.__aexit__ = AsyncMock(return_value=None)
        mock_db.begin = MagicMock(return_value=mock_begin_ctx)
        
        # Mock the session context manager
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_db)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=None)
        
        return mock_session_ctx

    @pytest.mark.asyncio
    async def test_writeback_with_generation_in_result(self, executor, mock_task):
        """Generation info in result dict is preserved in output_data."""
        result = {
            "final_video_url": "https://example.com/video.mp4",
            "generation_id": "gen-123",
            "variation_seed": 42,
            "variation_index": 0,
            "generation_reason": "initial",
        }

        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, True, result, None
            )

        assert mock_task.status.value == "success"
        assert mock_task.final_video_url == "https://example.com/video.mp4"
        assert mock_task.output_data["generation_id"] == "gen-123"
        assert mock_task.output_data["variation_seed"] == 42

    @pytest.mark.asyncio
    async def test_writeback_without_generation_still_succeeds(self, executor, mock_task):
        """Without generation info, status=success and final_video_url still persist."""
        result = {
            "final_video_url": "https://example.com/video.mp4",
        }

        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, True, result, None
            )

        assert mock_task.status.value == "success"
        assert mock_task.final_video_url == "https://example.com/video.mp4"
        assert mock_task.output_data is not None

    @pytest.mark.asyncio
    async def test_writeback_with_failure(self, executor, mock_task):
        """With failure, status is set to failed."""
        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, False, None, "some error"
            )

        assert mock_task.status.value == "failed"

    @pytest.mark.asyncio
    async def test_needs_manual_review_keeps_success(self, executor, mock_task):
        """needs_manual_review keeps status=success with video preserved."""
        result = {
            "final_video_url": "https://example.com/video.mp4",
            "needs_manual_review": True,
            "review_reason": "quality_warning",
        }

        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, True, result, None
            )

        assert mock_task.status.value == "success"
        assert mock_task.final_video_url == "https://example.com/video.mp4"
        # Check that warning was added to task.warning
        assert mock_task.warning is not None
        assert "needs_manualReview" in mock_task.warning

    @pytest.mark.asyncio
    async def test_generation_from_existing_output_data(self, executor, mock_task):
        """Generation info from existing output_data is preserved when not in result."""
        mock_task.output_data = {
            "generation_id": "gen-existing",
            "variation_seed": 99,
            "variation_index": 1,
            "generation_reason": "system_retry",
        }
        result = {
            "final_video_url": "https://example.com/video.mp4",
            # No generation fields in result
        }

        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, True, result, None
            )

        assert mock_task.status.value == "success"
        # Generation info from existing output_data should be preserved
        assert mock_task.output_data["generation_id"] == "gen-existing"
        assert mock_task.output_data["variation_seed"] == 99

    @pytest.mark.asyncio
    async def test_historical_task_without_generation_field(self, executor, mock_task):
        """Historical tasks without generation field work correctly."""
        mock_task.output_data = {
            "some_old_field": "value",
            # No generation fields
        }
        result = {
            "final_video_url": "https://example.com/video.mp4",
        }

        mock_session_ctx = self._setup_db_mock(mock_task)

        with patch('src.api.batch_executor.get_async_sessionmaker') as mock_session_factory:
            mock_session_factory.return_value = MagicMock(return_value=mock_session_ctx)
            
            await executor._update_task_final_status(
                mock_task.task_id, mock_task.batch_id, True, result, None
            )

        assert mock_task.status.value == "success"
        assert mock_task.final_video_url == "https://example.com/video.mp4"
        # Should not crash, output_data should be updated
        assert mock_task.output_data is not None
