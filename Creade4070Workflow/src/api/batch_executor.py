"""
Batch task executor for video generation workflow.

This module provides the BatchExecutor class that manages the execution of batch tasks,
including concurrency control, state management, and error handling.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional
from sqlalchemy import select, and_, update, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload


def ensure_utc_aware(dt: Optional[datetime]) -> Optional[datetime]:
    """
    Normalize a datetime to UTC-aware.
    
    Rules:
    - None → None
    - naive datetime → assume UTC, attach tzinfo
    - aware datetime → convert to UTC
    
    This prevents "can't subtract offset-naive and offset-aware datetimes" errors
    when comparing datetimes from different sources (database, user input, etc.).
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        # Naive datetime: assume it's UTC and attach tzinfo
        return dt.replace(tzinfo=timezone.utc)
    else:
        # Aware datetime: convert to UTC
        return dt.astimezone(timezone.utc)


def utc_now() -> datetime:
    """Return current UTC time as a naive datetime (for DB DateTime columns without timezone).
    
    The database columns (started_at, completed_at, etc.) are defined as DateTime
    without timezone=True. Storing aware datetimes would cause asyncpg/PostgreSQL
    to raise: 'cannot use a timezone-aware datetime in a timestamp without time zone column'.
    
    For Python-level comparisons, use ensure_utc_aware() to normalize datetimes
    read from the database before comparing with datetime.now(timezone.utc).
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)

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


async def claim_task_for_execution(
    task_id: uuid.UUID,
    run_id: uuid.UUID,
) -> tuple[bool, dict]:
    """
    Atomically claim a task for execution using a dedicated session.
    
    This prevents race conditions where multiple callers (start_batch,
    _trigger_next_pending_task, _refill_batch_slots) might try to execute
    the same task concurrently.
    
    The claim is atomic: only the caller that successfully transitions
    status from PENDING to RUNNING gets to execute the task.
    
    Uses a dedicated session to ensure isolation from the main session.
    
    Args:
        task_id: Task ID to claim
        run_id: Run ID to assign (used as execution lease)
        
    Returns:
        Tuple of (success: bool, diagnostic_info: dict)
    """
    from storage.database.batch_models import BatchTask, BatchTaskStatus
    from storage.database.db import get_async_sessionmaker
    from sqlalchemy import update, select
    
    diagnostic_info = {
        "task_id": str(task_id),
        "task_id_type": type(task_id).__name__,
        "run_id": str(run_id),
        "run_id_type": type(run_id).__name__,
        "pending_status_value": BatchTaskStatus.PENDING.value,
        "pending_status_type": type(BatchTaskStatus.PENDING).__name__,
    }
    
    # Use a dedicated session to ensure isolation
    async with get_async_sessionmaker()() as db:
        # First, query the task to get its current state for diagnostics
        query_result = await db.execute(
            select(BatchTask.task_id, BatchTask.batch_id, BatchTask.status, BatchTask.run_id)
            .where(BatchTask.task_id == task_id)
        )
        task_row = query_result.first()
        
        if task_row:
            diagnostic_info["actual_task_id"] = str(task_row.task_id)
            diagnostic_info["actual_task_id_type"] = type(task_row.task_id).__name__
            diagnostic_info["actual_batch_id"] = str(task_row.batch_id)
            diagnostic_info["actual_status"] = task_row.status
            diagnostic_info["actual_status_type"] = type(task_row.status).__name__
            diagnostic_info["actual_run_id"] = str(task_row.run_id) if task_row.run_id else None
            diagnostic_info["status_matches_pending"] = (task_row.status == BatchTaskStatus.PENDING.value)
            diagnostic_info["run_id_matches"] = (
                task_row.run_id == run_id if task_row.run_id else False
            )
        else:
            diagnostic_info["task_not_found"] = True
        
        # Atomic update: only claim if status is still PENDING
        # Use .value to ensure we're comparing strings, not enum objects
        result = await db.execute(
            update(BatchTask)
            .where(
                BatchTask.task_id == task_id,
                BatchTask.status == BatchTaskStatus.PENDING.value,
            )
            .values(
                status=BatchTaskStatus.RUNNING.value,
                run_id=run_id,
                started_at=utc_now(),
            )
        )
        
        diagnostic_info["update_rowcount"] = result.rowcount
        diagnostic_info["sql_condition"] = f"task_id={task_id} AND status={BatchTaskStatus.PENDING.value}"
        
        if result.rowcount == 1:
            await db.commit()
            logger.info(f"Task {task_id} claimed for execution, run_id={run_id}")
            return True, diagnostic_info
        else:
            # Determine the specific failure reason
            if task_row is None:
                diagnostic_info["claim_failure_reason"] = "TASK_NOT_FOUND"
            elif not diagnostic_info.get("status_matches_pending", False):
                diagnostic_info["claim_failure_reason"] = f"STATUS_NOT_PENDING (actual={task_row.status})"
            else:
                diagnostic_info["claim_failure_reason"] = f"UPDATE_ROWCOUNT_ZERO (rowcount={result.rowcount})"
            logger.warning(f"Task {task_id} claim failed: rowcount={result.rowcount}, diagnostic={diagnostic_info}")
            await db.rollback()
            return False, diagnostic_info


async def verify_run_lease(
    db: AsyncSession,
    task_id: uuid.UUID,
    expected_run_id: uuid.UUID,
) -> bool:
    """
    Verify that the current run_id matches the expected run_id.
    
    This is used to ensure that status updates (success, failure, rollback)
    are only applied by the current execution lease holder.
    
    Args:
        db: Database session
        task_id: Task ID to verify
        expected_run_id: Expected run_id (the lease)
        
    Returns:
        True if the lease is valid, False otherwise
    """
    from storage.database.batch_models import BatchTask
    
    result = await db.execute(
        select(BatchTask.run_id).where(BatchTask.task_id == task_id)
    )
    current_run_id = result.scalar_one_or_none()
    
    if current_run_id == expected_run_id:
        return True
    
    logger.warning(
        f"Run lease mismatch for task {task_id}: "
        f"expected={expected_run_id}, current={current_run_id}"
    )
    return False


async def _revert_task_to_pending(
    db: AsyncSession,
    task_id: uuid.UUID,
    error_code: str,
    error_message: str = "",
) -> None:
    """
    Revert a task to PENDING status after a submission failure.
    
    This is used when a task was atomically claimed (RUNNING) but the
    execution submission failed. The task is reverted to PENDING so it
    can be retried.
    
    Args:
        db: Database session
        task_id: Task ID to revert
        error_code: Error code to record
        error_message: Error message to record
    """
    from storage.database.batch_models import BatchTask, BatchTaskStatus
    from sqlalchemy import update
    
    try:
        result = await db.execute(
            update(BatchTask)
            .where(
                BatchTask.task_id == task_id,
                BatchTask.status == BatchTaskStatus.RUNNING,
            )
            .values(
                status=BatchTaskStatus.PENDING,
                run_id=None,
                started_at=None,
                error_code=error_code,
                error_message=error_message[:500] if error_message else "",
            )
        )
        await db.commit()
        if result.rowcount == 1:
            logger.info(f"Reverted task {task_id} to PENDING after {error_code}")
        else:
            logger.warning(f"Task {task_id} revert failed: not in RUNNING status (rowcount={result.rowcount})")
    except Exception as e:
        logger.error(f"Failed to revert task {task_id}: {e}")


async def submit_task_to_execution(
    db: AsyncSession,
    task: BatchTask,
    graph_service: "GraphService",
    run_id: uuid.UUID,
) -> tuple[bool, str]:
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
        Tuple of (success: bool, method: str) where method is "native", "fallback", or "none"
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
                return True, "native"
        except Exception as e:
            logger.warning(f"Native async submit failed for task {task.task_id}: {e}, trying fallback")
    
    # Fallback: use asyncio.create_task
    try:
        from storage.database.db import get_async_sessionmaker
        
        # Capture task info before entering background (task object may be detached from session)
        captured_task_id = task.task_id
        captured_batch_id = task.batch_id
        captured_run_id = run_id
        captured_graph_service = graph_service
        
        async def _run_task_in_background():
            """Background task runner that creates its own DB session."""
            try:
                executor = BatchExecutor(captured_graph_service)
                await executor._execute_claimed_task(
                    batch_id=captured_batch_id,
                    task_id=captured_task_id,
                    run_id=captured_run_id,
                )
            except Exception as e:
                logger.error(f"Background task {captured_task_id} failed with exception: {e}", exc_info=True)
                # Revert task to PENDING so it can be retried
                try:
                    async with get_async_sessionmaker()() as revert_db:
                        async with revert_db.begin():
                            result = await revert_db.execute(
                                select(BatchTask).where(BatchTask.task_id == captured_task_id)
                            )
                            revert_task = result.scalar_one_or_none()
                            if revert_task and revert_task.status == BatchTaskStatus.RUNNING.value:
                                revert_task.status = BatchTaskStatus.PENDING
                                revert_task.run_id = None
                                revert_task.started_at = None
                                revert_task.error_code = "FALLBACK_EXCEPTION"
                                revert_task.error_message = f"Background execution failed: {str(e)[:500]}"
                                logger.info(f"Reverted task {captured_task_id} to PENDING after background failure")
                except Exception as revert_err:
                    logger.error(f"Failed to revert task {captured_task_id}: {revert_err}")
        
        asyncio.create_task(_run_task_in_background())
        logger.info(f"Starting task {task.task_id} via asyncio.create_task fallback, run_id={run_id}")
        return True, "fallback"
    except Exception as e:
        logger.error(f"Fallback submit failed for task {task.task_id}: {e}")
        return False, "none"


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
    
    pending_count = sum(1 for t in tasks if t.status == BatchTaskStatus.PENDING.value)
    queued_count = sum(1 for t in tasks if t.status == BatchTaskStatus.QUEUED.value)
    running_count = sum(1 for t in tasks if t.status == BatchTaskStatus.RUNNING.value)
    success_count = sum(1 for t in tasks if t.status == BatchTaskStatus.SUCCESS.value)
    warning_count = sum(1 for t in tasks if t.status == BatchTaskStatus.WARNING.value)
    failed_count = sum(1 for t in tasks if t.status == BatchTaskStatus.FAILED.value)
    
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

        # Detect and recover orphaned RUNNING tasks (running > 30 minutes with no progress)
        # Wrapped in try/except to ensure datetime errors never block batch scheduling
        orphan_timeout = timedelta(minutes=30)
        orphan_count = 0
        try:
            now = datetime.now(timezone.utc)  # UTC-aware datetime
            for t in batch.tasks:
                if t.status == BatchTaskStatus.RUNNING.value and t.started_at:
                    started_at_aware = ensure_utc_aware(t.started_at)
                    running_duration = now - started_at_aware
                    if running_duration > orphan_timeout:
                        logger.warning(
                            f"Detected orphan task {t.task_id}: RUNNING for {running_duration}, "
                            f"resetting to PENDING for recovery"
                        )
                        t.status = BatchTaskStatus.PENDING.value
                        t.run_id = None
                        t.started_at = None
                        t.error_code = "ORPHAN_RECOVERY"
                        t.error_message = f"Task was RUNNING for {running_duration} with no progress, reset for retry"
                        orphan_count += 1
            
            if orphan_count > 0:
                await db.commit()
                logger.info(f"Recovered {orphan_count} orphan task(s) for batch {batch_id}")
        except Exception as e:
            logger.error(
                f"Orphan detection failed for batch {batch_id} (non-blocking, continuing): {e}",
                exc_info=True,
            )

        # Count real task statistics from database (after orphan recovery)
        pending_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.PENDING.value)
        running_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.RUNNING.value)
        success_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.SUCCESS.value)
        failed_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.FAILED.value)

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
        if batch.status != BatchJobStatus.RUNNING.value:
            batch.status = BatchJobStatus.RUNNING.value
            batch.started_at = batch.started_at or datetime.utcnow()
            await db.commit()

        # Get pending tasks
        pending_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.PENDING.value]

        if not pending_tasks:
            # No tasks to execute, mark as complete
            batch.status = BatchJobStatus.SUCCESS.value
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
        
        # Calculate available slots using real running count from this same query
        real_running_count = running_count  # Already computed from batch.tasks above
        real_pending_count = pending_count  # Already computed from batch.tasks above
        available_slots = max(0, concurrency - real_running_count)
        
        logger.info(
            f"start_batch {batch_id}: "
            f"real_pending_count={real_pending_count}, real_running_count={real_running_count}, "
            f"concurrency={concurrency}, available_slots={available_slots}"
        )
        
        if available_slots == 0:
            return {
                "batch_id": str(batch_id),
                "status": batch.status,
                "submitted_count": 0,
                "message": f"No available slots (running={real_running_count}, concurrency={concurrency})",
                "statistics": {
                    "pending": real_pending_count,
                    "running": real_running_count,
                    "success": success_count,
                    "failed": failed_count,
                },
            }
        
        # Get pending tasks
        pending_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.PENDING.value]
        
        # Select tasks to submit (up to available_slots)
        tasks_to_submit = pending_tasks[:available_slots]
        selected_task_ids = [str(t.task_id) for t in tasks_to_submit]
        
        logger.info(
            f"start_batch {batch_id}: selected {len(tasks_to_submit)} tasks: {selected_task_ids}"
        )
        
        if not tasks_to_submit:
            return {
                "batch_id": str(batch_id),
                "status": batch.status,
                "submitted_count": 0,
                "message": "No pending tasks to execute",
                "statistics": {
                    "pending": real_pending_count,
                    "running": real_running_count,
                    "success": success_count,
                    "failed": failed_count,
                },
            }
        
        submitted_count = 0
        native_async_count = 0
        fallback_count = 0
        claim_failed_count = 0
        claim_failures = []
        
        for task in tasks_to_submit:
            try:
                # Generate run_id for this task execution
                run_id = uuid.uuid4()
                
                # Atomically claim the task (PENDING → RUNNING)
                # This prevents race conditions with concurrent start_batch,
                # _trigger_next_pending_task, or _refill_batch_slots calls
                claimed, diagnostic_info = await claim_task_for_execution(task.task_id, run_id)
                if not claimed:
                    claim_failed_count += 1
                    claim_failures.append(diagnostic_info)
                    logger.warning(f"Task {task.task_id} claim failed: {diagnostic_info}")
                    continue
                
                # Task is now atomically claimed and committed as RUNNING
                # Submit to execution system
                success, method = await submit_task_to_execution(
                    db=db,
                    task=task,
                    graph_service=self.graph_service,
                    run_id=run_id,
                )
                if success:
                    submitted_count += 1
                    if method == "native":
                        native_async_count += 1
                    elif method == "fallback":
                        fallback_count += 1
                    logger.info(f"Starting task {task.task_id} via {method}, run_id={run_id}")
                else:
                    logger.error(f"Failed to submit task {task.task_id}: submit_task_to_execution returned False")
                    # Revert task status to PENDING in a new transaction
                    await _revert_task_to_pending(db, task.task_id, "SUBMIT_FAILED")
            except Exception as e:
                logger.error(f"Failed to submit task {task.task_id}: {e}")
                # Revert task status to PENDING in a new transaction
                await _revert_task_to_pending(db, task.task_id, "SUBMIT_EXCEPTION")
                continue
        
        await db.commit()
        
        # Update batch counts
        await self._update_batch_counts_safe(batch_id)
        
        remaining_count = real_pending_count - submitted_count
        
        logger.info(
            f"Batch {batch_id} started: "
            f"selected_count={len(tasks_to_submit)}, submitted_count={submitted_count}, "
            f"native_async_count={native_async_count}, fallback_count={fallback_count}, "
            f"remaining_count={remaining_count}"
        )
        
        return {
            "batch_id": str(batch_id),
            "status": batch.status,
            "total_count": batch.total_count,
            "selected_count": len(tasks_to_submit),
            "submitted_count": submitted_count,
            "native_async_count": native_async_count,
            "fallback_count": fallback_count,
            "claim_failed_count": claim_failed_count,
            "claim_failures": claim_failures,
            "remaining_count": remaining_count,
            "concurrency": concurrency,
            "statistics": {
                "pending": real_pending_count - submitted_count,
                "running": real_running_count + submitted_count,
                "success": success_count,
                "failed": failed_count,
            },
            "message": f"已提交 {submitted_count} 个任务（native={native_async_count}, fallback={fallback_count}），剩余 {remaining_count} 个等待中",
        }

    async def _execute_batch_tasks(self, db: AsyncSession, batch: BatchJob):
        """
        Execute all pending tasks in a batch with concurrency control.

        Args:
            db: Database session
            batch: Batch job instance
        """
        pending_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.PENDING.value]

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
                    if locked_task.status != BatchTaskStatus.PENDING.value:
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
            run_id=run_id,
        )

        # Step 4: Update batch counts with a NEW short-lived session
        await self._update_batch_counts_safe(batch_id)

        # Step 5: Trigger next pending task if there's capacity
        await self._trigger_next_pending_task(batch_id)

        logger.info(f"Task {task_id} execution completed")

    async def _execute_claimed_task(
        self,
        batch_id: uuid.UUID,
        task_id: uuid.UUID,
        run_id: uuid.UUID,
    ):
        """
        Execute a task that has already been claimed (status=RUNNING, run_id set).
        
        This is used by the fallback path in submit_task_to_execution where the task
        has already been set to RUNNING by start_batch, so we skip the claiming step
        and go directly to workflow execution.
        
        Uses run_id as execution lease: only the caller with matching run_id can execute.
        This prevents old/duplicate executors from running or overwriting new state.
        
        Args:
            batch_id: Batch job ID
            task_id: Task ID
            run_id: Run ID for this execution (execution lease)
        """
        logger.info(f"Executing claimed task {task_id} for batch {batch_id}, run_id={run_id}")
        
        # Fetch task and batch info using short-lived sessions
        task_input = None
        existing_output_data = None
        try:
            async with get_async_sessionmaker()() as fetch_db:
                result = await fetch_db.execute(
                    select(BatchTask).where(BatchTask.task_id == task_id)
                )
                task = result.scalar_one_or_none()
                if not task:
                    logger.error(f"Task {task_id} not found in database")
                    return
                
                # Verify task is still RUNNING (not reverted by another process)
                if task.status != BatchTaskStatus.RUNNING.value:
                    logger.warning(
                        f"Task {task_id} is no longer RUNNING (status={task.status}), skipping execution"
                    )
                    return
                
                # Verify run_id lease: only the caller with matching run_id can execute
                if task.run_id != run_id:
                    logger.warning(
                        f"Task {task_id} run_id mismatch: expected={run_id}, actual={task.run_id}. "
                        f"This is a duplicate/old executor, skipping execution."
                    )
                    return
                
                # Capture input data and existing output_data before session closes
                task_input = task.input_data or {}
                existing_output_data = task.output_data or {}
        except Exception as e:
            logger.error(f"Failed to fetch task {task_id} for execution: {e}", exc_info=True)
            return
        
        # Create or restore generation info
        from generation import (
            create_generation, create_retry_generation,
            GenerationReason,
        )
        
        existing_gen_id = existing_output_data.get("generation_id")
        if existing_gen_id:
            # Retry: restore existing generation (same seed = same result)
            generation = create_retry_generation(
                source_generation_id=existing_gen_id,
                source_variation_seed=existing_output_data.get("variation_seed", 0),
                variation_index=existing_output_data.get("variation_index", 0),
                generation_reason=GenerationReason.SYSTEM_RETRY,
            )
            logger.info(f"Task {task_id} retry: restoring generation {existing_gen_id[:8]}...")
        else:
            # New execution: create new generation
            generation = create_generation(
                reason=GenerationReason.NEW_BATCH,
                source_task_id=str(task_id),
                source_batch_id=str(batch_id),
            )
            logger.info(f"Task {task_id} new generation: {generation.generation_id[:8]}..., seed={generation.variation_seed}")
        
        # CRITICAL: Persist generation info to output_data BEFORE running workflow.
        # This ensures that if the worker crashes mid-execution, the seed is preserved
        # and retry will restore the same generation (same seed = same result).
        # Use atomic UPDATE to prevent race conditions with concurrent workers.
        try:
            from sqlalchemy import update as sa_update
            async with get_async_sessionmaker()() as persist_db:
                gen_snapshot = {
                    "generation_id": generation.generation_id,
                    "variation_seed": generation.variation_seed,
                    "variation_index": generation.variation_index,
                    "generation_reason": generation.generation_reason,
                }
                # Merge with existing output_data (preserve any prior fields)
                merged_output = dict(existing_output_data)
                merged_output.update(gen_snapshot)
                
                result = await persist_db.execute(
                    sa_update(BatchTask)
                    .where(
                        BatchTask.task_id == task_id,
                        BatchTask.run_id == run_id,  # Only update if we still own the lease
                    )
                    .values(output_data=merged_output)
                )
                await persist_db.commit()
                
                if result.rowcount == 0:
                    logger.warning(
                        f"Task {task_id} generation persistence failed (lease lost?). "
                        f"Continuing with in-memory generation."
                    )
                else:
                    logger.info(
                        f"Task {task_id} generation persisted: gen={generation.generation_id[:8]}..., "
                        f"seed={generation.variation_seed}, reason={generation.generation_reason}"
                    )
        except Exception as e:
            logger.error(f"Task {task_id} failed to persist generation: {e}", exc_info=True)
            # Don't fail the task - continue with in-memory generation
        
        # Run the workflow WITHOUT holding any database session
        workflow_result = None
        workflow_error = None
        workflow_success = False
        
        try:
            workflow_input = {
                "script_text": task_input.get("script_text", ""),
                "run_id": str(run_id),
                "script_source": "manual",
                "variation_seed": generation.variation_seed,
                "generation_id": generation.generation_id,
                "task_id": str(task_id),
            }
            
            from coze_coding_utils.runtime_ctx.context import new_context
            ctx = new_context("batch_task")
            ctx.run_id = str(run_id)
            
            logger.info(f"Running workflow for claimed task {task_id} with run_id {run_id}")
            workflow_result = await self.graph_service.run(workflow_input, ctx)
            
            if workflow_result.get("status") == "success":
                workflow_success = True
                logger.info(f"Claimed task {task_id} completed successfully")
            else:
                raw_error = (
                    workflow_result.get("fail_reason")
                    or workflow_result.get("error")
                    or workflow_result.get("message")
                    or None
                )
                workflow_error = _sanitize_error_message(raw_error)
                logger.error(
                    f"Claimed task {task_id} failed: "
                    f"batch_id={batch_id}, run_id={run_id}, "
                    f"status={workflow_result.get('status', 'unknown')}, "
                    f"fail_reason={workflow_error}"
                )
        
        except asyncio.CancelledError:
            workflow_error = "Task was cancelled"
            logger.warning(f"Claimed task {task_id} was cancelled")
        
        except Exception as e:
            workflow_error = _sanitize_error_message(str(e))
            logger.error(f"Claimed task {task_id} exception: {e}", exc_info=True)
        
        # Update final status
        await self._update_task_final_status(
            task_id=task_id,
            batch_id=batch_id,
            success=workflow_success,
            result=workflow_result,
            error=workflow_error,
            run_id=run_id,
        )
        
        # Update batch counts
        await self._update_batch_counts_safe(batch_id)
        
        # Trigger next pending task
        await self._trigger_next_pending_task(batch_id)
        
        logger.info(f"Claimed task {task_id} execution completed")

    async def _trigger_next_pending_task(self, batch_id: uuid.UUID):
        """
        Trigger the next pending task if there's capacity in the concurrency slot.
        This is called after a task completes to ensure pending tasks are picked up.
        
        Uses atomic claim to prevent race conditions with concurrent callers.
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
                        BatchTask.status.in_([BatchTaskStatus.RUNNING.value, BatchTaskStatus.QUEUED.value])
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
                        BatchTask.status == BatchTaskStatus.PENDING.value
                    )
                    .order_by(BatchTask.created_at)
                    .limit(1)
                )
                task = pending_result.scalar_one_or_none()
                if not task:
                    return  # No pending tasks

                # Atomically claim the task (PENDING → RUNNING)
                run_id = uuid.uuid4()
                claimed, diagnostic_info = await claim_task_for_execution(task.task_id, run_id)
                if not claimed:
                    logger.warning(f"Task {task.task_id} was already claimed by another caller: {diagnostic_info}")
                    return

                # Task is now atomically claimed and committed as RUNNING
                # Submit to execution system
                success, method = await submit_task_to_execution(
                    db=db,
                    task=task,
                    graph_service=self.graph_service,
                    run_id=run_id,
                )
                if success:
                    logger.info(f"Starting task {task.task_id} via {method}, run_id={run_id}")
                else:
                    logger.warning(f"Failed to trigger next pending task {task.task_id}")
                    # Revert task status to PENDING
                    await _revert_task_to_pending(db, task.task_id, "TRIGGER_SUBMIT_FAILED")

        except Exception as e:
            logger.error(f"Error triggering next pending task for batch {batch_id}: {e}")

    async def _update_task_final_status(
        self,
        task_id: uuid.UUID,
        batch_id: uuid.UUID,
        success: bool,
        result: Optional[Dict[str, Any]],
        error: Optional[str],
        run_id: Optional[uuid.UUID] = None,
        max_retries: int = 3,
    ):
        """
        Update task final status with retry logic for connection errors.
        
        Uses run_id as execution lease: only updates if the task's current run_id
        matches the expected run_id. This prevents old/duplicate executors from
        overwriting new state.

        Args:
            task_id: Task ID
            batch_id: Batch ID
            success: Whether the task succeeded
            result: Workflow result (if successful)
            error: Error message (if failed)
            run_id: Expected run_id (execution lease). If provided, verifies lease before updating.
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

                        # Verify run_id lease if provided
                        if run_id is not None and task.run_id != run_id:
                            logger.warning(
                                f"Task {task_id} run_id mismatch during status update: "
                                f"expected={run_id}, actual={task.run_id}. "
                                f"Skipping status update (old/duplicate executor)."
                            )
                            return
                            logger.error(f"Task {task_id} not found for status update")
                            return

                        if success:
                            task.status = BatchTaskStatus.SUCCESS
                            task.completed_at = datetime.utcnow()
                            task.final_video_url = result.get("final_video_url") if result else None
                            # Generation info should already be in result from workflow output
                            # or from the earlier persistence in _execute_claimed_task.
                            # Do NOT reference an undefined 'generation' variable here.
                            if result is not None:
                                # Ensure generation fields are present (may already be in result)
                                # If not, try to read from task's existing output_data
                                existing_output = task.output_data or {}
                                for gen_field in ("generation_id", "variation_seed", "variation_index", "generation_reason"):
                                    if gen_field not in result and gen_field in existing_output:
                                        result[gen_field] = existing_output[gen_field]
                            task.output_data = result
                            # Preserve warnings from quality check
                            warnings = result.get("warnings") if result else None
                            if warnings and isinstance(warnings, list):
                                task.warning = "; ".join(str(w) for w in warnings)
                            # Add manual review warning if needs_manual_review is set
                            if result and result.get("needs_manual_review"):
                                review_note = "needs_manualReview"
                                if task.warning:
                                    task.warning = f"{task.warning}; {review_note}"
                                else:
                                    task.warning = review_note
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
        run_id: Optional[uuid.UUID] = None,
    ):
        """
        Mark a task as failed with a new short-lived session.
        
        Uses run_id as execution lease: only marks as failed if the task's current
        run_id matches the expected run_id. This prevents old/duplicate executors
        from overwriting new state.

        Args:
            task_id: Task ID
            batch_id: Batch ID
            error_code: Error code
            error_message: Error message
            run_id: Expected run_id (execution lease). If provided, verifies lease before updating.
        """
        try:
            async with get_async_sessionmaker()() as error_db:
                async with error_db.begin():
                    result = await error_db.execute(
                        select(BatchTask).where(BatchTask.task_id == task_id)
                    )
                    task = result.scalar_one_or_none()
                    if task and task.status == BatchTaskStatus.RUNNING.value:
                        # Verify run_id lease if provided
                        if run_id is not None and task.run_id != run_id:
                            logger.warning(
                                f"Task {task_id} run_id mismatch during mark_failed: "
                                f"expected={run_id}, actual={task.run_id}. "
                                f"Skipping (old/duplicate executor)."
                            )
                            return
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
        success_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.SUCCESS.value)
        failed_count = sum(1 for t in batch.tasks if t.status == BatchTaskStatus.FAILED.value)

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
        if task.status != BatchTaskStatus.FAILED.value:
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
        failed_tasks = [t for t in batch.tasks if t.status == BatchTaskStatus.FAILED.value]

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
        timeout_threshold = utc_now() - timedelta(minutes=TASK_RUNNING_TIMEOUT_MINUTES)

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
            task.completed_at = utc_now()
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
