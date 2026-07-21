"""
Batch task executor
===================
Handles batch task scheduling, execution, and state management.
Designed for serverless environment with database-based coordination.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

from sqlalchemy import select, update, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database.batch_models import (
    BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus,
)
from storage.database.db import get_async_sessionmaker

logger = logging.getLogger(__name__)

# Timeout for tasks stuck in running state (minutes)
TASK_RUNNING_TIMEOUT_MINUTES = 30


class BatchExecutor:
    """Executor for batch job tasks."""

    def __init__(self, graph_service):
        """
        Initialize batch executor.

        Args:
            graph_service: GraphService instance for running video workflows
        """
        self.graph_service = graph_service

    async def start_batch(self, db: AsyncSession, batch_id: uuid.UUID) -> Dict[str, Any]:
        """
        Start executing a batch job.

        Args:
            db: Database session
            batch_id: Batch job ID

        Returns:
            Result with batch status and task count
        """
        # Get batch job
        result = await db.execute(
            select(BatchJob).where(BatchJob.batch_id == batch_id)
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        # Check if already started
        if batch.status != BatchJobStatus.CREATED:
            return {
                "batch_id": str(batch.batch_id),
                "status": batch.status,
                "message": f"Batch already started (status: {batch.status})",
            }

        # Update batch status to running
        batch.status = BatchJobStatus.RUNNING
        batch.started_at = datetime.utcnow()
        await db.commit()

        # Start executing tasks
        await self._execute_batch_tasks(db, batch)

        # Refresh batch to get updated stats
        await db.refresh(batch)

        return {
            "batch_id": str(batch.batch_id),
            "status": batch.status,
            "total_count": batch.total_count,
            "success_count": batch.success_count,
            "failed_count": batch.failed_count,
        }

    async def _execute_batch_tasks(self, db: AsyncSession, batch: BatchJob):
        """
        Execute all pending tasks in a batch with concurrency control.

        Args:
            db: Database session
            batch: Batch job instance
        """
        concurrency = batch.concurrency
        semaphore = asyncio.Semaphore(concurrency)

        # Get all pending tasks
        result = await db.execute(
            select(BatchTask)
            .where(
                and_(
                    BatchTask.batch_id == batch.batch_id,
                    BatchTask.status == BatchTaskStatus.PENDING,
                )
            )
            .order_by(BatchTask.row_number)
        )
        pending_tasks = list(result.scalars().all())

        if not pending_tasks:
            logger.info(f"No pending tasks for batch {batch.batch_id}")
            await self._update_batch_final_status(db, batch)
            return

        # Create async tasks for execution
        async_tasks = []
        for task in pending_tasks:
            async_task = asyncio.create_task(
                self._execute_single_task_with_semaphore(
                    db, batch, task, semaphore
                )
            )
            async_tasks.append(async_task)

        # Wait for all tasks to complete
        await asyncio.gather(*async_tasks, return_exceptions=True)

        # Update batch final status
        await self._update_batch_final_status(db, batch)

    async def _execute_single_task_with_semaphore(
        self,
        db: AsyncSession,
        batch: BatchJob,
        task: BatchTask,
        semaphore: asyncio.Semaphore,
    ):
        """
        Execute a single task with semaphore for concurrency control.

        Args:
            db: Database session
            batch: Batch job instance
            task: Task to execute
            semaphore: Semaphore for concurrency control
        """
        async with semaphore:
            await self._execute_single_task(db, batch, task)

    async def _execute_single_task(
        self,
        db: AsyncSession,
        batch: BatchJob,
        task: BatchTask,
    ):
        """
        Execute a single batch task.

        Args:
            db: Database session
            batch: Batch job instance
            task: Task to execute
        """
        task_id = task.task_id
        logger.info(f"Starting task {task_id} for batch {batch.batch_id}")

        # Claim task atomically using SELECT FOR UPDATE
        try:
            # Use a fresh session for this task
            async with get_async_sessionmaker()() as task_db:
                # Lock and update task status
                result = await task_db.execute(
                    select(BatchTask)
                    .where(BatchTask.task_id == task_id)
                    .with_for_update()
                )
                locked_task = result.scalar_one_or_none()

                if not locked_task:
                    logger.error(f"Task {task_id} not found")
                    return

                # Check if already running or completed
                if locked_task.status != BatchTaskStatus.PENDING:
                    logger.warning(
                        f"Task {task_id} already in status {locked_task.status}, skipping"
                    )
                    return

                # Update to running
                locked_task.status = BatchTaskStatus.RUNNING
                locked_task.started_at = datetime.utcnow()
                locked_task.error_code = None
                locked_task.error_message = None
                await task_db.commit()

                # Update batch running count
                await self._update_batch_counts(task_db, batch.batch_id)

                # Execute the video workflow
                try:
                    run_id = uuid.uuid4()
                    locked_task.run_id = run_id
                    await task_db.commit()

                    # Prepare input for workflow - include run_id for directory isolation
                    workflow_input = {
                        "script_text": task.input_data.get("script_text", ""),
                        "run_id": str(run_id),  # Pass run_id to workflow for directory isolation
                        "script_source": "manual",  # Batch tasks use manual script mode
                    }

                    # Create context for this run
                    from coze_coding_utils.runtime_ctx.context import new_context
                    ctx = new_context("batch_task")
                    ctx.run_id = str(run_id)

                    # Run the workflow
                    logger.info(f"Running workflow for task {task_id} with run_id {run_id}")
                    workflow_result = await self.graph_service.run(workflow_input, ctx)

                    # Check result
                    if workflow_result.get("status") == "success":
                        # Success
                        locked_task.status = BatchTaskStatus.SUCCESS
                        locked_task.completed_at = datetime.utcnow()
                        locked_task.final_video_url = workflow_result.get("final_video_url")
                        locked_task.output_data = workflow_result
                        logger.info(f"Task {task_id} completed successfully")
                    else:
                        # Failed
                        error_msg = workflow_result.get("error", "Unknown error")
                        locked_task.status = BatchTaskStatus.FAILED
                        locked_task.completed_at = datetime.utcnow()
                        locked_task.error_code = "WORKFLOW_ERROR"
                        locked_task.error_message = str(error_msg)
                        logger.error(f"Task {task_id} failed: {error_msg}")

                except asyncio.CancelledError:
                    # Task was cancelled
                    locked_task.status = BatchTaskStatus.FAILED
                    locked_task.completed_at = datetime.utcnow()
                    locked_task.error_code = "CANCELLED"
                    locked_task.error_message = "Task was cancelled"
                    logger.warning(f"Task {task_id} was cancelled")

                except Exception as e:
                    # Unexpected error
                    locked_task.status = BatchTaskStatus.FAILED
                    locked_task.completed_at = datetime.utcnow()
                    locked_task.error_code = "EXCEPTION"
                    locked_task.error_message = str(e)
                    logger.error(f"Task {task_id} exception: {e}", exc_info=True)

                # Commit final status
                await task_db.commit()

                # Update batch counts
                await self._update_batch_counts(task_db, batch.batch_id)

        except Exception as e:
            logger.error(f"Failed to execute task {task_id}: {e}", exc_info=True)
            # Try to mark task as failed
            try:
                async with get_async_sessionmaker()() as error_db:
                    result = await error_db.execute(
                        select(BatchTask).where(BatchTask.task_id == task_id)
                    )
                    failed_task = result.scalar_one_or_none()
                    if failed_task and failed_task.status == BatchTaskStatus.RUNNING:
                        failed_task.status = BatchTaskStatus.FAILED
                        failed_task.completed_at = datetime.utcnow()
                        failed_task.error_code = "EXECUTION_ERROR"
                        failed_task.error_message = str(e)
                        await error_db.commit()
                        await self._update_batch_counts(error_db, batch.batch_id)
            except Exception as inner_e:
                logger.error(f"Failed to mark task {task_id} as failed: {inner_e}")

    async def _update_batch_counts(self, db: AsyncSession, batch_id: uuid.UUID):
        """
        Update batch job counts based on task statuses.

        Args:
            db: Database session
            batch_id: Batch job ID
        """
        # Count tasks by status
        result = await db.execute(
            select(
                BatchTask.status,
                func.count(BatchTask.task_id),
            )
            .where(BatchTask.batch_id == batch_id)
            .group_by(BatchTask.status)
        )
        status_counts = dict(result.all())

        pending_count = status_counts.get(BatchTaskStatus.PENDING, 0)
        running_count = status_counts.get(BatchTaskStatus.RUNNING, 0)
        success_count = status_counts.get(BatchTaskStatus.SUCCESS, 0)
        failed_count = status_counts.get(BatchTaskStatus.FAILED, 0)

        # Update batch
        await db.execute(
            update(BatchJob)
            .where(BatchJob.batch_id == batch_id)
            .values(
                pending_count=pending_count,
                running_count=running_count,
                success_count=success_count,
                failed_count=failed_count,
            )
        )
        await db.commit()

    async def _update_batch_final_status(self, db: AsyncSession, batch: BatchJob):
        """
        Update batch job final status after all tasks complete.

        Args:
            db: Database session
            batch: Batch job instance
        """
        # Refresh batch to get latest counts
        await db.refresh(batch)

        # Determine final status
        if batch.failed_count == 0:
            batch.status = BatchJobStatus.SUCCESS
        elif batch.success_count == 0:
            batch.status = BatchJobStatus.FAILED
        else:
            batch.status = BatchJobStatus.PARTIAL_FAILED

        batch.completed_at = datetime.utcnow()
        await db.commit()

    async def retry_task(
        self,
        db: AsyncSession,
        batch_id: uuid.UUID,
        task_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Retry a failed task.

        Args:
            db: Database session
            batch_id: Batch job ID
            task_id: Task ID to retry

        Returns:
            Result with task status
        """
        # Get task
        result = await db.execute(
            select(BatchTask).where(
                and_(
                    BatchTask.task_id == task_id,
                    BatchTask.batch_id == batch_id,
                )
            )
        )
        task = result.scalar_one_or_none()

        if not task:
            raise ValueError(f"Task {task_id} not found")

        # Check if failed
        if task.status != BatchTaskStatus.FAILED:
            raise ValueError(f"Can only retry failed tasks, current status: {task.status}")

        # Get batch
        result = await db.execute(
            select(BatchJob).where(BatchJob.batch_id == batch_id)
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        # Reset task to pending
        task.status = BatchTaskStatus.PENDING
        task.retry_count += 1
        task.started_at = None
        task.completed_at = None
        task.error_code = None
        task.error_message = None
        task.run_id = None
        task.output_data = None
        task.final_video_url = None
        await db.commit()

        # Update batch counts
        await self._update_batch_counts(db, batch_id)

        # If batch is not running, we need to re-execute
        if batch.status != BatchJobStatus.RUNNING:
            batch.status = BatchJobStatus.RUNNING
            batch.completed_at = None
            await db.commit()

            # Execute this task
            await self._execute_single_task(db, batch, task)

            # Refresh and update batch final status
            await db.refresh(batch)
            await self._update_batch_final_status(db, batch)

        return {
            "task_id": str(task.task_id),
            "status": task.status,
            "retry_count": task.retry_count,
        }

    async def retry_failed_tasks(
        self,
        db: AsyncSession,
        batch_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Retry all failed tasks in a batch.

        Args:
            db: Database session
            batch_id: Batch job ID

        Returns:
            Result with retry count
        """
        # Get batch
        result = await db.execute(
            select(BatchJob).where(BatchJob.batch_id == batch_id)
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        # Get all failed tasks
        result = await db.execute(
            select(BatchTask)
            .where(
                and_(
                    BatchTask.batch_id == batch_id,
                    BatchTask.status == BatchTaskStatus.FAILED,
                )
            )
            .order_by(BatchTask.row_number)
        )
        failed_tasks = list(result.scalars().all())

        if not failed_tasks:
            return {
                "batch_id": str(batch_id),
                "retried_count": 0,
                "message": "No failed tasks to retry",
            }

        # Reset all failed tasks to pending
        for task in failed_tasks:
            task.status = BatchTaskStatus.PENDING
            task.retry_count += 1
            task.started_at = None
            task.completed_at = None
            task.error_code = None
            task.error_message = None
            task.run_id = None
            task.output_data = None
            task.final_video_url = None

        await db.commit()

        # Update batch counts
        await self._update_batch_counts(db, batch_id)

        # Set batch to running
        batch.status = BatchJobStatus.RUNNING
        batch.completed_at = None
        await db.commit()

        # Execute all retried tasks
        await self._execute_batch_tasks(db, batch)

        # Refresh and get final status
        await db.refresh(batch)

        return {
            "batch_id": str(batch_id),
            "retried_count": len(failed_tasks),
            "status": batch.status,
            "success_count": batch.success_count,
            "failed_count": batch.failed_count,
        }

    async def recover_stuck_tasks(self, db: AsyncSession) -> Dict[str, Any]:
        """
        Recover tasks stuck in running state.

        Args:
            db: Database session

        Returns:
            Result with recovered task count
        """
        # Find tasks stuck in running for too long
        timeout_threshold = datetime.utcnow() - timedelta(minutes=TASK_RUNNING_TIMEOUT_MINUTES)

        result = await db.execute(
            select(BatchTask)
            .where(
                and_(
                    BatchTask.status == BatchTaskStatus.RUNNING,
                    BatchTask.started_at < timeout_threshold,
                )
            )
        )
        stuck_tasks = list(result.scalars().all())

        if not stuck_tasks:
            return {"recovered_count": 0, "message": "No stuck tasks found"}

        # Mark stuck tasks as failed
        for task in stuck_tasks:
            task.status = BatchTaskStatus.FAILED
            task.completed_at = datetime.utcnow()
            task.error_code = "TIMEOUT"
            task.error_message = f"Task stuck in running state for > {TASK_RUNNING_TIMEOUT_MINUTES} minutes"

        await db.commit()

        # Update batch counts for affected batches
        affected_batch_ids = set(task.batch_id for task in stuck_tasks)
        for batch_id in affected_batch_ids:
            result = await db.execute(
                select(BatchJob).where(BatchJob.batch_id == batch_id)
            )
            batch = result.scalar_one_or_none()
            if batch:
                await self._update_batch_counts(db, batch_id)
                await self._update_batch_final_status(db, batch)

        logger.info(f"Recovered {len(stuck_tasks)} stuck tasks")

        return {
            "recovered_count": len(stuck_tasks),
            "task_ids": [str(task.task_id) for task in stuck_tasks],
        }


__all__ = ["BatchExecutor"]
