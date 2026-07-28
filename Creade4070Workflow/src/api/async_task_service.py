"""
Async task integration for batch tasks.

This module provides integration with the Coze native async task system
(coze_coding_utils.async_tasks) for reliable long-running batch task execution.
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional

from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database.batch_models import BatchTask, BatchTaskStatus

logger = logging.getLogger(__name__)

# Import the native async task runtime
try:
    from coze_coding_utils.async_tasks import AsyncTaskRuntime
    from coze_coding_utils.async_tasks.instance import get_async_task_runtime
    ASYNC_TASKS_AVAILABLE = True
except ImportError:
    ASYNC_TASKS_AVAILABLE = False
    logger.warning("coze_coding_utils.async_tasks not available, async task submission disabled")


class AsyncTaskService:
    """
    Service for submitting and polling batch tasks using the native async task system.
    """

    def __init__(self, graph_service: "GraphService"):
        """
        Initialize the async task service.

        Args:
            graph_service: GraphService instance for running workflows
        """
        self.graph_service = graph_service
        self.runtime = get_async_task_runtime() if ASYNC_TASKS_AVAILABLE else None

    async def submit_task(
        self,
        db: AsyncSession,
        task: BatchTask,
        deadline_sec: int = 1800,
    ) -> Dict[str, Any]:
        """
        Submit a batch task to the native async task system.

        Args:
            db: Database session
            task: BatchTask to submit
            deadline_sec: Task deadline in seconds (default 1800 = 30 minutes)

        Returns:
            Dict with async_task_id, status, and message

        Raises:
            RuntimeError: If async task system is not available
            ValueError: If task is not in a valid state for submission
        """
        if not ASYNC_TASKS_AVAILABLE or self.runtime is None:
            raise RuntimeError("Native async task system is not available")

        # Validate task state
        if task.status not in [BatchTaskStatus.PENDING, BatchTaskStatus.QUEUED, BatchTaskStatus.FAILED]:
            raise ValueError(f"Task status {task.status} is not valid for submission")

        # Prepare workflow input using unified builder
        from api.batch_executor import build_workflow_input
        workflow_input = build_workflow_input(task)
        workflow_input["batch_id"] = str(task.batch_id)
        workflow_input["retry_count"] = task.retry_count

        # Generate a unique run_id for this execution
        run_id = uuid.uuid4()

        # Submit to native async task system
        try:
            async_task_id = await self.runtime.submit(
                workflow_input=workflow_input,
                graph_service=self.graph_service,
                deadline_sec=deadline_sec,
                metadata={
                    "batch_id": str(task.batch_id),
                    "task_id": str(task.task_id),
                    "run_id": str(run_id),
                    "retry_count": task.retry_count,
                },
            )

            # Update task with async_task_id and run_id
            task.async_task_id = async_task_id
            task.run_id = run_id
            task.status = BatchTaskStatus.QUEUED
            task.started_at = datetime.utcnow()
            task.error_message = None  # Clear previous errors
            task.error_code = None

            await db.commit()

            logger.info(
                f"Submitted task {task.task_id} to async system, "
                f"async_task_id={async_task_id}, deadline={deadline_sec}s"
            )

            return {
                "task_id": str(task.task_id),
                "async_task_id": async_task_id,
                "run_id": str(run_id),
                "status": BatchTaskStatus.QUEUED,
                "retry_count": task.retry_count,
                "message": "任务已进入异步执行队列",
            }

        except Exception as e:
            logger.error(f"Failed to submit task {task.task_id} to async system: {e}")
            # Mark task as failed with the actual error
            task.status = BatchTaskStatus.FAILED
            task.error_message = f"异步任务提交失败: {str(e)}"
            task.error_code = "ASYNC_SUBMIT_FAILED"
            await db.commit()
            raise

    async def poll_task_status(
        self,
        db: AsyncSession,
        task: BatchTask,
    ) -> Dict[str, Any]:
        """
        Poll the status of a batch task from the native async task system.

        Args:
            db: Database session
            task: BatchTask to poll

        Returns:
            Dict with status, final_video_url, warning, error_message, etc.
        """
        if not task.async_task_id:
            return {
                "status": task.status,
                "final_video_url": task.final_video_url,
                "warning": task.warning,
                "error_message": task.error_message,
            }

        if not ASYNC_TASKS_AVAILABLE or self.runtime is None:
            # Fallback to database status if async system not available
            return {
                "status": task.status,
                "final_video_url": task.final_video_url,
                "warning": task.warning,
                "error_message": task.error_message,
            }

        try:
            # Query native async task status
            async_task = await self.runtime.get(task.async_task_id)

            if not async_task:
                logger.warning(f"Async task {task.async_task_id} not found")
                return {
                    "status": task.status,
                    "final_video_url": task.final_video_url,
                    "warning": task.warning,
                    "error_message": task.error_message,
                }

            # Map native status to business status
            native_status = async_task.get("status")
            native_result = async_task.get("result")
            native_error = async_task.get("error")

            # Update task based on native status
            if native_status == "pending":
                task.status = BatchTaskStatus.QUEUED

            elif native_status == "running":
                task.status = BatchTaskStatus.RUNNING

            elif native_status == "completed":
                # Parse result
                if native_result and isinstance(native_result, dict):
                    final_video_url = native_result.get("final_video_url")
                    warning = native_result.get("warning")

                    if final_video_url:
                        task.status = BatchTaskStatus.SUCCESS
                        task.final_video_url = final_video_url
                        task.warning = warning
                        task.output_data = native_result  # Save complete output data
                        task.error_message = None
                        task.error_code = None
                        task.completed_at = datetime.utcnow()
                        logger.info(f"Task {task.task_id} completed successfully")
                    else:
                        # Result structure is invalid
                        task.status = BatchTaskStatus.FAILED
                        task.error_message = "任务完成但未生成视频：结果结构不合法"
                        task.error_code = "INVALID_RESULT"
                        task.completed_at = datetime.utcnow()
                        logger.error(f"Task {task.task_id} completed but result is invalid")
                else:
                    task.status = BatchTaskStatus.FAILED
                    task.error_message = "任务完成但结果为空或格式错误"
                    task.error_code = "EMPTY_RESULT"
                    task.completed_at = datetime.utcnow()
                    logger.error(f"Task {task.task_id} completed but result is empty")

            elif native_status == "failed":
                task.status = BatchTaskStatus.FAILED
                # Preserve the full error message from native system
                if native_error:
                    task.error_message = str(native_error)
                else:
                    task.error_message = "异步任务执行失败，错误信息未提供"
                task.error_code = "ASYNC_TASK_FAILED"
                task.completed_at = datetime.utcnow()
                logger.error(f"Task {task.task_id} failed: {native_error}")

            elif native_status == "timeout":
                task.status = BatchTaskStatus.FAILED
                task.error_message = "异步任务超过执行期限（1800秒）"
                task.error_code = "ASYNC_TASK_TIMEOUT"
                task.completed_at = datetime.utcnow()
                logger.error(f"Task {task.task_id} timed out")

            else:
                logger.warning(f"Unknown native status {native_status} for task {task.task_id}")

            await db.commit()

            return {
                "status": task.status,
                "final_video_url": task.final_video_url,
                "warning": task.warning,
                "error_message": task.error_message,
                "async_task_id": task.async_task_id,
                "native_status": native_status,
            }

        except Exception as e:
            logger.error(f"Failed to poll task {task.task_id}: {e}")
            # Return current database status on error
            return {
                "status": task.status,
                "final_video_url": task.final_video_url,
                "warning": task.warning,
                "error_message": task.error_message,
                "poll_error": str(e),
            }


# Global instance
_async_task_service: Optional[AsyncTaskService] = None


def get_async_task_service(graph_service: "GraphService") -> AsyncTaskService:
    """Get or create the global async task service instance."""
    global _async_task_service
    if _async_task_service is None:
        _async_task_service = AsyncTaskService(graph_service)
    return _async_task_service
