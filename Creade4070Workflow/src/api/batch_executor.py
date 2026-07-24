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

# Maximum length for error_message stored in DB
MAX_ERROR_MESSAGE_LENGTH = 2000


def _sanitize_error_message(raw) -> str:
    """Sanitize error message to a safe, JSON-serializable string.
    
    - Always returns a string
    - Strips non-string types to their str() representation
    - Truncates to MAX_ERROR_MESSAGE_LENGTH
    - Never returns empty string (falls back to 'Unknown error')
    """
    if raw is None:
        return "Unknown error"
    if isinstance(raw, str):
        msg = raw.strip()
    else:
        # Convert non-string to string safely
        try:
            msg = str(raw).strip()
        except Exception:
            msg = "Unknown error"
    
    if not msg:
        return "Unknown error"
    
    # Truncate to prevent oversized DB writes
    if len(msg) > MAX_ERROR_MESSAGE_LENGTH:
        msg = msg[:MAX_ERROR_MESSAGE_LENGTH] + "...[truncated]"
    
    return msg


def _extract_diagnostic_fields(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Extract minimal JSON-safe diagnostic fields from workflow result.
    
    Only extracts: status, failed_node, fail_reason, error_code.
    Converts UUID to str, datetime to ISO, Enum to value.
    Returns None if result is None.
    """
    if not result:
        return None
    
    def _safe_str(val) -> Optional[str]:
        if val is None:
            return None
        if isinstance(val, str):
            return val
        if isinstance(val, (int, float, bool)):
            return str(val)
        if isinstance(val, uuid.UUID):
            return str(val)
        if isinstance(val, datetime):
            return val.isoformat()
        if hasattr(val, 'value'):  # Enum
            return str(val.value)
        return str(val)
    
    diagnostic = {}
    
    # status
    status = result.get("status")
    if status is not None:
        diagnostic["status"] = _safe_str(status)
    
    # failed_node (failure_category in quality check)
    failed_node = result.get("failure_category") or result.get("failed_node")
    if failed_node is not None:
        diagnostic["failed_node"] = _safe_str(failed_node)
    
    # fail_reason
    fail_reason = result.get("fail_reason") or result.get("error") or result.get("message")
    if fail_reason is not None:
        diagnostic["fail_reason"] = _safe_str(fail_reason)
    
    # error_code (from workflow, not our internal code)
    error_code = result.get("error_code")
    if error_code is not None:
        diagnostic["error_code"] = _safe_str(error_code)
    
    return diagnostic if diagnostic else None


async def submit_task_to_execution(
    db: AsyncSession,
    task: BatchTask,
    graph_service: "GraphService",
    run_id: uuid.UUID,
) -> bool:
    """
    Unified task submission function.
    
    Tries native async system first, falls back to asyncio.create_task if unavailable.
    This ensures tasks can be executed regardless of whether the native async system is available.
    
    Args:
        db: Database session
        task: Task to submit
        graph_service: GraphService for running workflows
        run_id: Run ID for this execution
        
    Returns:
        True if submitted successfully, False otherwise
    """
    from api.async_task_service import get_async_task_service, ASYNC_TASKS_AVAILABLE
    
    # Try native async system first
    if ASYNC_TASKS_AVAILABLE:
        try:
            async_task_service = get_async_task_service(graph_service)
            if async_task_service.runtime is not None:
                await async_task_service.submit_task(
                    task_id=str(task.task_id),
                    input_data=task.input_data or {},
                    deadline_sec=1800,
                )
                logger.info(f"Submitted task {task.task_id} to native async system")
                return True
        except Exception as e:
            logger.warning(f"Native async submit failed for task {task.task_id}: {e}, trying fallback")
    
    # Fallback: use asyncio.create_task
    try:
        from storage.database.db import get_async_sessionmaker
        
        async def _run_task_in_background():
            """Background task runner that creates its own DB session."""
            async with get_async_sessionmaker()() as bg_db:
                executor = BatchExecutor(graph_service)
                try:
                    await executor._execute_single_task(bg_db, task, run_id)
                except Exception as e:
                    logger.error(f"Background task {task.task_id} failed: {e}")
        
        asyncio.create_task(_run_task_in_background())
        logger.info(f"Submitted task {task.task_id} via asyncio.create_task fallback")
        return True
    except Exception as e:
        logger.error(f"Fallback submit failed for task {task.task_id}: {e}")
        return False


async def update_batch_counts(db: AsyncSession, batch_id: uuid.UUID) -> None:
    """
    Update batch job counts based on actual task statuses.
    
    This ensures the batch job's pending_count, running_count, etc. are always
    in sync with the actual task statuses.
    """
    from storage.database.batch_models import BatchJob, BatchTask, BatchTaskStatus, BatchJobStatus
    
    # Count tasks by status
    result = await db.execute(
        select(BatchTask).where(BatchTask.batch_id == batch_id)
    )
    tasks = list(result.scalars().all())
    
    pending_count = sum(1 for t in tasks if t.status == BatchTaskStatus.PENDING)
    queued_count = sum(1 for t in tasks if t.status == BatchTaskStatus.QUEUED)
    running_count = sum(1 for t in tasks if t.status == BatchTaskStatus.RUNNING)
    success_count = sum(1 for t in tasks if t.status == BatchTaskStatus.SUCCESS)
    warning_count = sum(1 for t in tasks if t.status == BatchTaskStatus.WARNING)
    failed_count = sum(1 for t in tasks if t.status == BatchTaskStatus.FAILED)
    
    # Update batch job
    batch_result = await db.execute(
        select(BatchJob).where(BatchJob.batch_id == batch_id)
    )
    batch = batch_result.scalar_one_or_none()
    if batch:
        batch.pending_count = pending_count
        batch.running_count = running_count + queued_count  # queued counts as running
        batch.success_count = success_count
        batch.warning_count = warning_count
        batch.failed_count = failed_count
        
        # Update batch status based on task completion
        total = len(tasks)
        completed = success_count + warning_count + failed_count
        
        if completed == total and total > 0:
            # All tasks completed
            if failed_count == 0:
                batch.status = BatchJobStatus.SUCCESS
            elif success_count == 0 and warning_count == 0:
                batch.status = BatchJobStatus.FAILED
            else:
                batch.status = BatchJobStatus.PARTIAL_FAILED
            batch.completed_at = datetime.utcnow()
        elif running_count > 0 or queued_count > 0:
            batch.status = BatchJobStatus.RUNNING
        elif pending_count > 0:
            batch.status = BatchJobStatus.RUNNING  # Still has pending tasks
        
        await db.commit()
        logger.info(f"Updated batch {batch_id} counts: pending={pending_count}, running={running_count}, success={success_count}, failed={failed_count}")


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
        
        Only submits up to N tasks (concurrency limit) to the native async system.
        Remaining tasks stay as pending/queued until slots free up.

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

        # Count real task statistics from database
        pending_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.PENDING)
        running_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.RUNNING)
        success_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.SUCCESS)
        failed_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.FAILED)

        # Check if batch is already fully complete
        if pending_count == 0 and running_count == 0:
            return {
                "batch_id": str(batch.batch_id),
                "status": batch.status,
                "submitted_count": 0,
                "message": "Batch already complete (no pending or running tasks)",
                "statistics": {
                    "pending": pending_count,
                    "running": running_count,
                    "success": success_count,
                    "failed": failed_count,
                },
            }

        # Check if there are already running tasks (don't start more)
        if running_count > 0:
            return {
                "batch_id": str(batch.batch_id),
                "status": batch.status,
                "submitted_count": 0,
                "message": f"Batch already has {running_count} running task(s)",
                "statistics": {
                    "pending": pending_count,
                    "running": running_count,
                    "success": success_count,
                    "failed": failed_count,
                },
            }

        # Update batch to running (if not already)
        if batch.status != BatchJobStatus.RUNNING:
            batch.status = BatchJobStatus.RUNNING
            batch.started_at = batch.started_at or datetime.utcnow()
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

        # Get async task service (may be None if not available)
        from api.async_task_service import get_async_task_service
        async_task_service = get_async_task_service(self.graph_service)
        
        concurrency = batch.concurrency or 2
        submitted_count = 0
        
        # Use unified submission function for all tasks
        tasks_to_submit = pending_tasks[:concurrency]
        for task in tasks_to_submit:
            try:
                success = await submit_task_to_execution(
                    db=db,
                    task=task,
                    batch_id=batch_id,
                    graph_service=self.graph_service,
                    async_task_service=async_task_service,
                    executor=self,
                )
                if success:
                    submitted_count += 1
            except Exception as e:
                logger.error(f"Failed to submit task {task.task_id}: {e}")
                # Mark task as failed
                await self._mark_task_failed(task.task_id, batch_id, "SUBMIT_ERROR", str(e))
                continue

        # Update batch counts
        await self._update_batch_counts_safe(batch_id)

        logger.info(f"Batch {batch_id} started: submitted {submitted_count}/{len(pending_tasks)} tasks (concurrency={concurrency})")

        return {
            "batch_id": str(batch_id),
            "status": batch.status,
            "total_count": batch.total_count,
            "submitted_count": submitted_count,
            "pending_count": len(pending_tasks) - submitted_count,
            "concurrency": concurrency,
            "message": f"已提交 {submitted_count} 个任务到异步执行队列，剩余 {len(pending_tasks) - submitted_count} 个等待中",
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
                # Extract error from multiple possible fields (fail_reason > error > message)
                raw_error = (
                    workflow_result.get("fail_reason")
                    or workflow_result.get("error")
                    or workflow_result.get("message")
                    or None
                )
                workflow_error = _sanitize_error_message(raw_error)
                
                # Structured failure log
                logger.error(
                    f"Task {task_id} failed: "
                    f"batch_id={batch_id}, run_id={run_id}, "
                    f"status={workflow_result.get('status', 'unknown')}, "
                    f"failed_node={workflow_result.get('failure_category', 'unknown')}, "
                    f"fail_reason={workflow_error}"
                )

        except asyncio.CancelledError:
            workflow_error = "Task was cancelled"
            logger.warning(f"Task {task_id} was cancelled")

        except Exception as e:
            workflow_error = _sanitize_error_message(str(e))
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

        # Step 5: Trigger next pending task if there's capacity
        await self._trigger_next_pending_task(batch_id)

        logger.info(f"Task {task_id} execution completed")

    async def _trigger_next_pending_task(self, batch_id: uuid.UUID):
        """
        Trigger the next pending task if there's capacity in the concurrency slot.
        This is called after a task completes to ensure pending tasks are picked up.
        """
        try:
            async with get_async_sessionmaker()() as db:
                # Get batch with current task counts
                result = await db.execute(
                    select(BatchJob).where(BatchJob.batch_id == batch_id)
                )
                batch = result.scalar_one_or_none()
                if not batch:
                    return

                # Get current running count from actual tasks
                running_result = await db.execute(
                    select(func.count())
                    .select_from(BatchTask)
                    .where(
                        BatchTask.batch_id == batch_id,
                        BatchTask.status.in_([BatchTaskStatus.RUNNING, BatchTaskStatus.QUEUED])
                    )
                )
                running_count = running_result.scalar() or 0

                concurrency = batch.concurrency or 2
                if running_count >= concurrency:
                    return  # No capacity

                # Get next pending task
                pending_result = await db.execute(
                    select(BatchTask)
                    .where(
                        BatchTask.batch_id == batch_id,
                        BatchTask.status == BatchTaskStatus.PENDING
                    )
                    .order_by(BatchTask.created_at)
                    .limit(1)
                )
                task = pending_result.scalar_one_or_none()
                if not task:
                    return  # No pending tasks

                # Get async task service
                from api.async_task_service import get_async_task_service
                async_task_service = get_async_task_service(self.graph_service)

                # Submit the task using unified function
                success = await submit_task_to_execution(
                    db=db,
                    task=task,
                    batch_id=batch_id,
                    graph_service=self.graph_service,
                    async_task_service=async_task_service,
                    executor=self,
                )
                if success:
                    logger.info(f"Triggered next pending task {task.task_id} for batch {batch_id}")
                else:
                    logger.warning(f"Failed to trigger next pending task {task.task_id}")

        except Exception as e:
            logger.error(f"Error triggering next pending task for batch {batch_id}: {e}")

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
                            # Preserve warnings from quality check
                            warnings = result.get("warnings") if result else None
                            if warnings and isinstance(warnings, list):
                                task.warning = "; ".join(str(w) for w in warnings)
                        else:
                            # Check if video was actually generated despite status != "success"
                            # e.g. quality check returned "failed" but final_video_url exists
                            final_video_url = result.get("final_video_url") if result else None
                            if final_video_url:
                                # Video exists - treat as success with warning
                                task.status = BatchTaskStatus.SUCCESS
                                task.completed_at = datetime.utcnow()
                                task.final_video_url = final_video_url
                                task.output_data = result
                                # Build warning from fail_reason
                                fail_reason = (
                                    result.get("fail_reason")
                                    or result.get("error")
                                    or result.get("message")
                                    or "Quality check flagged issues"
                                )
                                task.warning = f"视频已生成但存在质量告警: {fail_reason}"
                                logger.warning(
                                    f"Task {task_id} has final_video_url but status={result.get('status')}, "
                                    f"keeping as SUCCESS with warning"
                                )
                            else:
                                # True failure - no video generated
                                task.status = BatchTaskStatus.FAILED
                                task.completed_at = datetime.utcnow()
                                task.error_code = "WORKFLOW_ERROR" if result else "EXCEPTION"
                                task.error_message = _sanitize_error_message(error)
                                # Save minimal diagnostic fields (not full output_data)
                                diagnostic = _extract_diagnostic_fields(result)
                                if diagnostic:
                                    task.output_data = diagnostic

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
        Updates all counts: pending, running, success, warning, failed.

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
                    pending_result = await count_db.execute(
                        select(BatchTask).where(
                            and_(
                                BatchTask.batch_id == batch_id,
                                BatchTask.status == BatchTaskStatus.PENDING,
                            )
                        )
                    )
                    batch.pending_count = len(pending_result.scalars().all())

                    running_result = await count_db.execute(
                        select(BatchTask).where(
                            and_(
                                BatchTask.batch_id == batch_id,
                                BatchTask.status == BatchTaskStatus.RUNNING,
                            )
                        )
                    )
                    batch.running_count = len(running_result.scalars().all())

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

                    # Update batch status based on counts
                    if batch.pending_count == 0 and batch.running_count == 0:
                        if batch.failed_count > 0:
                            batch.status = BatchJobStatus.PARTIAL_FAILED if batch.success_count > 0 else BatchJobStatus.FAILED
                        else:
                            batch.status = BatchJobStatus.SUCCESS
                    elif batch.running_count > 0 or batch.pending_count > 0:
                        batch.status = BatchJobStatus.RUNNING
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
    ) -> Dict[str, Any]:
        """
        Retry a failed task using the unified scheduling path.

        This method resets the task to PENDING and schedules it for execution
        using the same path as start_batch (native async or fallback).
        State changes only happen after successful scheduling.

        Args:
            db: Database session
            batch_id: Batch job ID
            task_id: Task ID to retry

        Returns:
            Result with task status
        """
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

        # Check concurrency limit: count running and queued tasks
        running_count = sum(1 for t in batch.tasks if t.status in [BatchTaskStatus.RUNNING, BatchTaskStatus.QUEUED])
        concurrency = batch.concurrency or 2
        if running_count >= concurrency:
            raise ValueError(
                f"Concurrency limit reached: {running_count}/{concurrency} tasks running. "
                f"Wait for a slot to free up before retrying."
            )

        # Check if native async task system is available
        from api.async_task_service import get_async_task_service, ASYNC_TASKS_AVAILABLE
        async_task_service = get_async_task_service(self.graph_service)
        use_native_async = ASYNC_TASKS_AVAILABLE and async_task_service.runtime is not None

        # Generate new run_id for this retry attempt
        new_run_id = uuid.uuid4()

        if use_native_async:
            # Submit to native async task system FIRST (before any state changes)
            try:
                submit_result = await async_task_service.submit_task(
                    db=db,
                    task=task,
                    deadline_sec=1800,  # 30 minutes
                )
            except Exception as e:
                logger.error(f"Failed to submit task {task_id} to async system: {e}")
                # Task remains in FAILED state - no state changes made
                raise RuntimeError(f"Failed to submit retry task: {e}") from e

            # Submission succeeded - NOW update task state
            task.retry_count += 1
            task.status = BatchTaskStatus.QUEUED
            task.run_id = new_run_id
            task.started_at = datetime.utcnow()
            task.completed_at = None
            task.error_code = None
            task.error_message = None
            task.output_data = None
            task.final_video_url = None
            task.warning = None
            task.async_task_id = submit_result.get("async_task_id")
        else:
            # Fallback: execute task directly using asyncio.create_task
            logger.info(f"Native async system not available, using direct execution fallback for retry task {task_id}")
            
            # Reset task to PENDING for the scheduler to pick up
            task.retry_count += 1
            task.status = BatchTaskStatus.PENDING
            task.run_id = new_run_id
            task.completed_at = None
            task.error_code = None
            task.error_message = None
            task.output_data = None
            task.final_video_url = None
            task.warning = None
            task.async_task_id = None
            
            # Create a background task to execute the workflow
            asyncio.create_task(
                self._execute_single_task_with_semaphore(batch, task),
                name=f"retry_task_{task_id}"
            )

        # Set batch to running
        batch.status = BatchJobStatus.RUNNING
        batch.completed_at = None

        await db.commit()

        logger.info(f"Task {task_id} retried (run_id={new_run_id}, native_async={use_native_async})")

        return {
            "task_id": str(task_id),
            "status": "queued" if use_native_async else "pending",
            "run_id": str(new_run_id),
            "retry_count": task.retry_count,
            "message": "Task submitted for retry",
        }

    async def retry_failed(
        self,
        db: AsyncSession,
        batch_id: uuid.UUID,
    ) -> Dict[str, Any]:
        """
        Retry all failed tasks in a batch using the unified scheduling path.

        This method resets failed tasks to PENDING and schedules them for execution
        using the same path as start_batch (native async or fallback).
        State changes only happen after successful scheduling.

        Args:
            db: Database session
            batch_id: Batch job ID

        Returns:
            Result with retried task count
        """
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

        # Check concurrency limit: count running and queued tasks
        running_count = sum(1 for t in batch.tasks if t.status in [BatchTaskStatus.RUNNING, BatchTaskStatus.QUEUED])
        concurrency = batch.concurrency or 2
        available_slots = max(0, concurrency - running_count)

        if available_slots == 0:
            raise ValueError(
                f"Concurrency limit reached: {running_count}/{concurrency} tasks running. "
                f"Wait for a slot to free up before retrying."
            )

        # Only retry up to available_slots tasks
        tasks_to_retry = failed_tasks[:available_slots]
        skipped_count = len(failed_tasks) - len(tasks_to_retry)

        # Check if native async task system is available
        from api.async_task_service import get_async_task_service, ASYNC_TASKS_AVAILABLE
        async_task_service = get_async_task_service(self.graph_service)
        use_native_async = ASYNC_TASKS_AVAILABLE and async_task_service.runtime is not None

        # Submit tasks FIRST, then update state only for successful submissions
        submitted_count = 0
        for task in tasks_to_retry:
            new_run_id = uuid.uuid4()
            
            if use_native_async:
                # Submit to native async task system FIRST
                try:
                    submit_result = await async_task_service.submit_task(
                        db=db,
                        task=task,
                        deadline_sec=1800,  # 30 minutes
                    )
                except Exception as e:
                    logger.error(f"Failed to submit task {task.task_id} to async system: {e}")
                    # Task remains in FAILED state - no state changes made
                    continue

                # Submission succeeded - NOW update task state
                task.retry_count += 1
                task.status = BatchTaskStatus.QUEUED
                task.run_id = new_run_id
                task.started_at = datetime.utcnow()
                task.completed_at = None
                task.error_code = None
                task.error_message = None
                task.output_data = None
                task.final_video_url = None
                task.warning = None
                task.async_task_id = submit_result.get("async_task_id")
            else:
                # Fallback: execute task directly using asyncio.create_task
                task.retry_count += 1
                task.status = BatchTaskStatus.PENDING
                task.run_id = new_run_id
                task.completed_at = None
                task.error_code = None
                task.error_message = None
                task.output_data = None
                task.final_video_url = None
                task.warning = None
                task.async_task_id = None
                
                # Create a background task to execute the workflow
                asyncio.create_task(
                    self._execute_single_task_with_semaphore(batch, task),
                    name=f"retry_task_{task.task_id}"
                )
            
            submitted_count += 1

        if submitted_count > 0:
            # Set batch to running
            batch.status = BatchJobStatus.RUNNING
            batch.completed_at = None

        await db.commit()

        # Update batch counts
        await self._update_batch_counts_safe(batch_id)

        logger.info(f"Submitted {submitted_count}/{len(failed_tasks)} failed tasks (native_async={use_native_async})")

        result_msg = f"Submitted {submitted_count} tasks for retry"
        if skipped_count > 0:
            result_msg += f" ({skipped_count} skipped due to concurrency limit)"

        return {
            "batch_id": str(batch_id),
            "retried_count": submitted_count,
            "status": "queued" if use_native_async else "pending",
            "message": f"{submitted_count} 个任务已进入执行队列",
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

    # ------------------------------------------------------------------
    # Helper methods for testing and reuse
    # ------------------------------------------------------------------

    def _can_retry_task(self, task) -> bool:
        """Check if a task can be retried (only failed tasks)."""
        return task.status == 'failed'

    def _increment_retry_count(self, task) -> int:
        """Increment retry count and return new value."""
        new_count = (task.retry_count or 0) + 1
        task.retry_count = new_count
        return new_count

    def _get_tasks_to_submit(self, tasks, concurrency: int) -> list:
        """
        Get tasks that should be submitted to the async system.
        Only returns pending tasks that don't already have an async_task_id,
        limited by the concurrency setting minus currently running tasks.
        """
        running_count = sum(1 for t in tasks if t.status in ('running', 'queued') and t.async_task_id)
        available_slots = max(0, concurrency - running_count)

        to_submit = []
        for task in tasks:
            if len(to_submit) >= available_slots:
                break
            if task.status in ('pending', 'created') and not task.async_task_id:
                to_submit.append(task)

        return to_submit
