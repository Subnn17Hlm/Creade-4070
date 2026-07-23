"""
Batch task executor for video generation workflow.

This module provides the BatchExecutor class that manages the execution of batch tasks,
including concurrency control, state management, and error handling.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, List, Optional
from sqlalchemy import select, and_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from storage.database.db import get_async_sessionmaker
from storage.database.batch_models import (
    BatchJob,
    BatchTask,
    BatchJobStatus,
    BatchTaskStatus,
)

# Configuration
MAX_CONCURRENT_TASKS = 4  # Default concurrency limit
TASK_RUNNING_TIMEOUT_MINUTES = 30  # Timeout for running tasks

# Logger
logger = logging.getLogger(__name__)


class BatchExecutor:
    """
    Executes batch tasks with concurrency control and state management.
    """

    def __init__(self, graph_service: "GraphService", max_concurrent: int = MAX_CONCURRENT_TASKS):
        """
        Initialize the batch executor.

        Args:
            graph_service: GraphService instance for running workflows
            max_concurrent: Maximum number of concurrent tasks (1-4)
        """
        self.graph_service = graph_service
        self.max_concurrent = min(max(1, max_concurrent), 4)  # Clamp to 1-4
        self._semaphore = asyncio.Semaphore(self.max_concurrent)
        self._running_tasks: Dict[str, asyncio.Task] = {}

    async def start_batch(self, db: AsyncSession, batch_id: uuid.UUID) -> Dict[str, Any]:
        """
        Start executing a batch job.

        Args:
            db: Database session
            batch_id: Batch job ID

        Returns:
            Result with batch status and task count
        """
        # Fetch batch
        result = await db.execute(
            select(BatchJob)
            .where(BatchJob.batch_id == batch_id)
            .options(selectinload(BatchJob.tasks))
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        if batch.status != BatchJobStatus.CREATED:
            return {
                "batch_id": str(batch.batch_id),
                "status": batch.status,
                "message": f"Batch already started (status: {batch.status})",
            }

        # Update batch to running
        batch.status = BatchJobStatus.RUNNING
        batch.started_at = datetime.utcnow()
        await db.commit()

        # Get pending tasks
        pending_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.PENDING]

        if not pending_tasks:
            # No tasks to execute, mark as complete
            batch.status = BatchJobStatus.SUCCESS
            batch.completed_at = datetime.utcnow()
            await db.commit()
            return {
                "batch_id": str(batch_id),
                "status": batch.status,
                "message": "No pending tasks to execute",
            }

        # Execute tasks with concurrency control
        await self._execute_batch_tasks(db, batch)

        # Refresh and get final status
        await db.refresh(batch)

        return {
            "batch_id": str(batch_id),
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
        pending_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.PENDING]

        # Create async tasks with semaphore for concurrency control
        async_tasks = []
        for task in pending_tasks:
            async_task = asyncio.create_task(
                self._execute_single_task_with_semaphore(batch, task)
            )
            async_tasks.append(async_task)
            self._running_tasks[str(task.task_id)] = async_task

        # Wait for all tasks to complete
        if async_tasks:
            await asyncio.gather(*async_tasks, return_exceptions=True)

        # Clean up running tasks
        for task in pending_tasks:
            self._running_tasks.pop(str(task.task_id), None)

        # Update batch final status
        await self._update_batch_final_status(db, batch)

    async def _execute_single_task_with_semaphore(
        self,
        batch: BatchJob,
        task: BatchTask,
    ):
        """
        Execute a single task with semaphore for concurrency control.

        Args:
            batch: Batch job instance
            task: Task to execute
        """
        async with self._semaphore:
            await self._execute_single_task(batch, task)

    async def _execute_single_task(
        self,
        batch: BatchJob,
        task: BatchTask,
    ):
        """
        Execute a single batch task with short-lived database sessions.

        This method uses independent short-lived sessions for each database operation
        to avoid holding connections open during long-running workflow execution.

        Args:
            batch: Batch job instance
            task: Task to execute
        """
        task_id = task.task_id
        batch_id = batch.batch_id
        logger.info(f"Starting task {task_id} for batch {batch_id}")

        # Step 1: Claim task atomically using SELECT FOR UPDATE (short-lived session)
        run_id = None
        try:
            async with get_async_sessionmaker()() as claim_db:
                async with claim_db.begin():
                    result = await claim_db.execute(
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
                    run_id = uuid.uuid4()
                    locked_task.status = BatchTaskStatus.RUNNING
                    locked_task.started_at = datetime.utcnow()
                    locked_task.run_id = run_id
                    locked_task.error_code = None
                    locked_task.error_message = None

                # Session is closed here, connection released

        except Exception as e:
            logger.error(f"Failed to claim task {task_id}: {e}", exc_info=True)
            await self._mark_task_failed(task_id, batch_id, "CLAIM_ERROR", str(e))
            return

        # Step 2: Run the workflow WITHOUT holding any database session
        workflow_result = None
        workflow_error = None
        workflow_success = False

        try:
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

            # Run the workflow (long-running, no DB session held)
            logger.info(f"Running workflow for task {task_id} with run_id {run_id}")
            workflow_result = await self.graph_service.run(workflow_input, ctx)

            # Check result
            if workflow_result.get("status") == "success":
                workflow_success = True
                logger.info(f"Task {task_id} completed successfully")
            else:
                workflow_error = workflow_result.get("error", "Unknown error")
                logger.error(f"Task {task_id} failed: {workflow_error}")

        except asyncio.CancelledError:
            workflow_error = "Task was cancelled"
            logger.warning(f"Task {task_id} was cancelled")

        except Exception as e:
            workflow_error = str(e)
            logger.error(f"Task {task_id} exception: {e}", exc_info=True)

        # Step 3: Update final status with a NEW short-lived session (with retry)
        await self._update_task_final_status(
            task_id=task_id,
            batch_id=batch_id,
            success=workflow_success,
            result=workflow_result,
            error=workflow_error,
        )

        # Step 4: Update batch counts with a NEW short-lived session
        await self._update_batch_counts_safe(batch_id)

        logger.info(f"Task {task_id} execution completed")

    async def _update_task_final_status(
        self,
        task_id: uuid.UUID,
        batch_id: uuid.UUID,
        success: bool,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        max_retries: int = 3,
    ):
        """
        Update task final status with retry logic for connection errors.

        Args:
            task_id: Task ID
            batch_id: Batch ID
            success: Whether the task succeeded
            result: Workflow result (if successful)
            error: Error message (if failed)
            max_retries: Maximum number of retries for connection errors
        """
        for attempt in range(max_retries):
            try:
                async with get_async_sessionmaker()() as status_db:
                    async with status_db.begin():
                        result_query = await status_db.execute(
                            select(BatchTask).where(BatchTask.task_id == task_id)
                        )
                        task = result_query.scalar_one_or_none()

                        if not task:
                            logger.error(f"Task {task_id} not found for status update")
                            return

                        if success:
                            task.status = BatchTaskStatus.SUCCESS
                            task.completed_at = datetime.utcnow()
                            task.final_video_url = result.get("final_video_url") if result else None
                            task.output_data = result
                        else:
                            task.status = BatchTaskStatus.FAILED
                            task.completed_at = datetime.utcnow()
                            task.error_code = "WORKFLOW_ERROR" if result else "EXCEPTION"
                            task.error_message = str(error) if error else "Unknown error"

                    # Session is closed here, connection released
                    return

            except Exception as e:
                error_name = type(e).__name__
                if "InterfaceError" in error_name or "connection" in str(e).lower():
                    logger.warning(
                        f"Connection error updating task {task_id} status (attempt {attempt + 1}/{max_retries}): {e}"
                    )
                    if attempt < max_retries - 1:
                        await asyncio.sleep(0.5 * (attempt + 1))  # Exponential backoff
                        continue
                else:
                    logger.error(f"Failed to update task {task_id} status: {e}", exc_info=True)
                    return

        # All retries failed
        logger.error(f"Failed to update task {task_id} status after {max_retries} retries")

    async def _mark_task_failed(
        self,
        task_id: uuid.UUID,
        batch_id: uuid.UUID,
        error_code: str,
        error_message: str,
    ):
        """
        Mark a task as failed with a new short-lived session.

        Args:
            task_id: Task ID
            batch_id: Batch ID
            error_code: Error code
            error_message: Error message
        """
        try:
            async with get_async_sessionmaker()() as error_db:
                async with error_db.begin():
                    result = await error_db.execute(
                        select(BatchTask).where(BatchTask.task_id == task_id)
                    )
                    task = result.scalar_one_or_none()
                    if task and task.status == BatchTaskStatus.RUNNING:
                        task.status = BatchTaskStatus.FAILED
                        task.completed_at = datetime.utcnow()
                        task.error_code = error_code
                        task.error_message = error_message
                # Session is closed here
        except Exception as e:
            logger.error(f"Failed to mark task {task_id} as failed: {e}", exc_info=True)

    async def _update_batch_counts_safe(self, batch_id: uuid.UUID):
        """
        Update batch counts with a new short-lived session.

        Args:
            batch_id: Batch ID
        """
        try:
            async with get_async_sessionmaker()() as count_db:
                async with count_db.begin():
                    # Get batch
                    result = await count_db.execute(
                        select(BatchJob).where(BatchJob.batch_id == batch_id)
                    )
                    batch = result.scalar_one_or_none()
                    if not batch:
                        return

                    # Count tasks by status
                    success_result = await count_db.execute(
                        select(BatchTask).where(
                            and_(
                                BatchTask.batch_id == batch_id,
                                BatchTask.status == BatchTaskStatus.SUCCESS,
                            )
                        )
                    )
                    batch.success_count = len(success_result.scalars().all())

                    failed_result = await count_db.execute(
                        select(BatchTask).where(
                            and_(
                                BatchTask.batch_id == batch_id,
                                BatchTask.status == BatchTaskStatus.FAILED,
                            )
                        )
                    )
                    batch.failed_count = len(failed_result.scalars().all())
                # Session is closed here
        except Exception as e:
            logger.error(f"Failed to update batch {batch_id} counts: {e}", exc_info=True)

    async def _update_batch_final_status(self, db: AsyncSession, batch: BatchJob):
        """
        Update batch final status based on task results.

        Args:
            db: Database session
            batch: Batch job instance
        """
        # Refresh batch to get latest task statuses
        await db.refresh(batch)

        # Count tasks by status
        success_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.SUCCESS)
        failed_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.FAILED)

        # Update batch counts
        batch.success_count = success_count
        batch.failed_count = failed_count

        # Determine final status
        if failed_count == 0:
            batch.status = BatchJobStatus.SUCCESS
        elif success_count == 0:
            batch.status = BatchJobStatus.FAILED
        else:
            batch.status = BatchJobStatus.PARTIAL_FAILED

        batch.completed_at = datetime.utcnow()
        await db.commit()

        logger.info(
            f"Batch {batch.batch_id} completed with status {batch.status} "
            f"(success={success_count}, failed={failed_count})"
        )

    async def retry_task(
        self,
        db: AsyncSession,
        batch_id: uuid.UUID,
        task_id: uuid.UUID,
        async_task_service: "AsyncTaskService" = None,
    ) -> Dict[str, Any]:
        """
        Retry a single failed task using native async task system.

        This method resets the task to queued and submits it to the native
        async task system. Returns immediately with HTTP 202.

        Args:
            db: Database session
            batch_id: Batch job ID
            task_id: Task ID to retry
            async_task_service: AsyncTaskService instance for native async submission

        Returns:
            Result with task status (queued for execution)
        """
        if async_task_service is None:
            raise ValueError("async_task_service is required for retry")

        # Fetch batch and task with row lock
        result = await db.execute(
            select(BatchJob)
            .where(BatchJob.batch_id == batch_id)
            .options(selectinload(BatchJob.tasks))
            .with_for_update()
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        task = next((t for t in batch.tasks if t.task_id == task_id), None)
        if not task:
            raise ValueError(f"Task {task_id} not found in batch {batch_id}")

        # Only allow retry for failed tasks
        if task.status != BatchTaskStatus.FAILED:
            raise ValueError(
                f"Task {task_id} is in {task.status} status, only failed tasks can be retried"
            )

        # Increment retry count and reset task state
        task.retry_count += 1
        task.started_at = None
        task.completed_at = None
        task.error_code = None
        task.error_message = None
        task.run_id = None
        task.output_data = None
        task.final_video_url = None
        task.warning = None
        task.async_task_id = None

        await db.commit()

        # Update batch counts
        await self._update_batch_counts(db, batch_id)

        # Set batch to running
        batch.status = BatchJobStatus.RUNNING
        batch.completed_at = None
        await db.commit()

        # Submit to native async task system
        try:
            result = await async_task_service.submit_task(
                db=db,
                task=task,
                deadline_sec=1800,  # 30 minutes
            )

            logger.info(f"Task {task_id} submitted to native async system")

            return result

        except Exception as e:
            logger.error(f"Failed to submit task {task_id} to async system: {e}")
            # Task is already marked as failed in submit_task
            raise

    async def retry_failed(
        self,
        db: AsyncSession,
        batch_id: uuid.UUID,
        async_task_service: "AsyncTaskService" = None,
    ) -> Dict[str, Any]:
        """
        Retry all failed tasks in a batch using native async task system.

        This method resets failed tasks to queued and submits them to the native
        async task system. Returns immediately with HTTP 202.

        Args:
            db: Database session
            batch_id: Batch job ID
            async_task_service: AsyncTaskService instance for native async submission

        Returns:
            Result with retried task count (queued for execution)
        """
        if async_task_service is None:
            raise ValueError("async_task_service is required for retry")

        # Fetch batch with row lock
        result = await db.execute(
            select(BatchJob)
            .where(BatchJob.batch_id == batch_id)
            .options(selectinload(BatchJob.tasks))
            .with_for_update()
        )
        batch = result.scalar_one_or_none()

        if not batch:
            raise ValueError(f"Batch {batch_id} not found")

        # Get failed tasks
        failed_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.FAILED]

        if not failed_tasks:
            return {
                "batch_id": str(batch_id),
                "retried_count": 0,
                "message": "No failed tasks to retry",
            }

        # Reset all failed tasks and submit to async system
        submitted_count = 0
        for task in failed_tasks:
            # Increment retry count and reset task state
            task.retry_count += 1
            task.started_at = None
            task.completed_at = None
            task.error_code = None
            task.error_message = None
            task.run_id = None
            task.output_data = None
            task.final_video_url = None
            task.warning = None
            task.async_task_id = None

            await db.commit()

            # Submit to native async task system
            try:
                await async_task_service.submit_task(
                    db=db,
                    task=task,
                    deadline_sec=1800,  # 30 minutes
                )
                submitted_count += 1
            except Exception as e:
                logger.error(f"Failed to submit task {task.task_id} to async system: {e}")
                # Task is already marked as failed in submit_task
                continue

        # Update batch counts
        await self._update_batch_counts(db, batch_id)

        # Set batch to running
        batch.status = BatchJobStatus.RUNNING
        batch.completed_at = None
        await db.commit()

        logger.info(f"Submitted {submitted_count}/{len(failed_tasks)} failed tasks to native async system")

        return {
            "batch_id": str(batch_id),
            "retried_count": submitted_count,
            "status": "queued",
            "message": f"{submitted_count} 个任务已进入异步执行队列",
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

    async def _update_batch_counts(self, db: AsyncSession, batch_id: uuid.UUID):
        """
        Update batch job counts based on task statuses.

        Args:
            db: Database session
            batch_id: Batch job ID
        """
        # Get batch
        result = await db.execute(
            select(BatchJob).where(BatchJob.batch_id == batch_id)
        )
        batch = result.scalar_one_or_none()
        if not batch:
            return

        # Count tasks by status
        success_result = await db.execute(
            select(BatchTask).where(
                and_(
                    BatchTask.batch_id == batch_id,
                    BatchTask.status == BatchTaskStatus.SUCCESS,
                )
            )
        )
        batch.success_count = len(success_result.scalars().all())

        failed_result = await db.execute(
            select(BatchTask).where(
                and_(
                    BatchTask.batch_id == batch_id,
                    BatchTask.status == BatchTaskStatus.FAILED,
                )
            )
        )
        batch.failed_count = len(failed_result.scalars().all())

        await db.commit()
