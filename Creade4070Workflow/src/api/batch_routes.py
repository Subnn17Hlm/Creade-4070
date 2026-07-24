"""Batch API routes."""
import uuid
import logging
from typing import Optional
from datetime import datetime

from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, and_
from sqlalchemy.orm import selectinload

from storage.database.db import get_db_session, get_async_sessionmaker
from storage.database.batch_models import BatchJob, BatchTask, BatchTaskStatus, BatchJobStatus
from api.batch_csv import validate_csv, MAX_BATCH_SIZE
from api.batch_service import BatchService
from api.batch_executor import BatchExecutor

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/batches', tags=['batches'])


async def _refill_batch_slots(db: AsyncSession, batch_id: uuid.UUID):
    """
    Refill batch execution slots: when tasks complete, submit next pending tasks.
    
    This ensures concurrency control: only N tasks run at a time.
    Uses SELECT FOR UPDATE to prevent duplicate submissions from concurrent polls.
    """
    # Get batch concurrency
    result = await db.execute(
        select(BatchJob).where(BatchJob.batch_id == batch_id)
    )
    batch = result.scalar_one_or_none()
    if not batch:
        return
    
    concurrency = batch.concurrency or 2
    
    # Count currently running/queued tasks (active slots)
    active_result = await db.execute(
        select(BatchTask).where(
            and_(
                BatchTask.batch_id == batch_id,
                BatchTask.status.in_([BatchTaskStatus.QUEUED, BatchTaskStatus.RUNNING]),
            )
        )
    )
    active_count = len(active_result.scalars().all())
    
    # Calculate available slots
    available_slots = max(0, concurrency - active_count)
    if available_slots == 0:
        return
    
    # Get pending tasks (ordered by row_number for deterministic ordering)
    pending_result = await db.execute(
        select(BatchTask)
        .where(
            and_(
                BatchTask.batch_id == batch_id,
                BatchTask.status == BatchTaskStatus.PENDING,
            )
        )
        .order_by(BatchTask.row_number)
        .limit(available_slots)
    )
    pending_tasks = list(pending_result.scalars().all())
    
    if not pending_tasks:
        # No more pending tasks - check if batch should be marked complete
        all_result = await db.execute(
            select(BatchTask).where(BatchTask.batch_id == batch_id)
        )
        all_tasks = list(all_result.scalars().all())
        has_active = any(t.status in [BatchTaskStatus.QUEUED, BatchTaskStatus.RUNNING] for t in all_tasks)
        if not has_active:
            # All tasks done
            success_count = sum(1 for t in all_tasks if t.status == BatchTaskStatus.SUCCESS)
            failed_count = sum(1 for t in all_tasks if t.status == BatchTaskStatus.FAILED)
            if failed_count == 0:
                batch.status = BatchJobStatus.SUCCESS
            elif success_count == 0:
                batch.status = BatchJobStatus.FAILED
            else:
                batch.status = BatchJobStatus.PARTIAL_FAILED
            batch.completed_at = datetime.utcnow()
            await db.commit()
        return
    
    # Submit next tasks using unified submission function
    from api.batch_executor import submit_task_to_execution, update_batch_counts, claim_task_for_execution, _revert_task_to_pending
    from main import service as graph_service
    
    submitted = 0
    for task in pending_tasks:
        try:
            # Atomically claim the task (PENDING → RUNNING)
            run_id = uuid.uuid4()
            async with get_async_sessionmaker()() as claim_db:
                claimed = await claim_task_for_execution(task.task_id, run_id)
                if not claimed:
                    logger.warning(f"Task {task.task_id} was already claimed by another caller during refill")
                    continue
                
                # Task is now atomically claimed and committed as RUNNING
                # Re-fetch the task in this session for submission
                refetch_result = await claim_db.execute(
                    select(BatchTask).where(BatchTask.task_id == task.task_id)
                )
                locked_task = refetch_result.scalar_one_or_none()
                if not locked_task:
                    logger.error(f"Task {task.task_id} not found after claim")
                    continue
                
                # Use unified submission function (native async or fallback)
                success, method = await submit_task_to_execution(
                    db=claim_db,
                    task=locked_task,
                    graph_service=graph_service,
                    run_id=run_id,
                )
                if success:
                    submitted += 1
                    logger.info(f"Starting task {locked_task.task_id} via {method}, run_id={run_id}")
                else:
                    # Revert task status to PENDING
                    await _revert_task_to_pending(claim_db, task.task_id, "REFILL_SUBMIT_FAILED")
        except Exception as e:
            logger.error(f"Failed to submit task {task.task_id} during refill: {e}")
            # Revert task status to PENDING
            try:
                async with get_async_sessionmaker()() as revert_db:
                    await _revert_task_to_pending(revert_db, task.task_id, "REFILL_EXCEPTION", str(e)[:500])
            except Exception as revert_err:
                logger.error(f"Failed to revert task {task.task_id}: {revert_err}")
            continue
    
    if submitted > 0:
        logger.info(f"Refilled batch {batch_id}: submitted {submitted} more tasks")
        # Update batch counts after successful submission
        async with get_async_sessionmaker()() as count_db:
            await update_batch_counts(count_db, batch_id)


@router.post('')
async def create_batch(
    file: UploadFile = File(...),
    concurrency: int = Form(default=2),
    idempotency_key: Optional[str] = Header(None, alias='Idempotency-Key'),
    db: AsyncSession = Depends(get_db_session),
):
    """Create a new batch job from CSV file.
    
    Args:
        file: CSV file upload
        concurrency: Concurrency level (1-4)
        idempotency_key: Optional idempotency key
        
    Returns:
        Batch job creation result
    """
    # Validate concurrency
    if concurrency < 1 or concurrency > 4:
        raise HTTPException(
            status_code=400,
            detail={'error': 'concurrency 必须在 1-4 之间'},
        )
    
    # Read file content
    content = await file.read()
    
    # Validate CSV
    result = validate_csv(content, filename=file.filename)
    
    if not result.success:
        # Build detailed error message
        error_details = []
        for e in result.errors:
            if e.row_number:
                error_details.append(f"第 {e.row_number} 行: {e.message}")
            else:
                error_details.append(e.message)
        
        error_summary = '; '.join(error_details)
        
        return JSONResponse(
            status_code=400,
            content={
                'error': f'CSV 校验失败: {error_summary}',
                'errors': [e.to_dict() for e in result.errors],
                'missing_columns': [col for col in ['script_text'] if col not in [h.strip() for h in (content.decode('utf-8').split('\n')[0].split(',') if content else [])]],
            },
        )
    
    # Create batch
    try:
        batch = await BatchService.create_batch(
            db=db,
            rows=result.rows,
            concurrency=concurrency,
            idempotency_key=idempotency_key,
            source_filename=file.filename,
        )
    except Exception as e:
        logger.error(f"Failed to create batch: {type(e).__name__}: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail={'error': f'创建批次失败: {str(e)}', 'type': type(e).__name__},
        )
    
    return {
        'batch_id': str(batch.batch_id),
        'status': batch.status,
        'total_count': batch.total_count,
        'concurrency': batch.concurrency,
        'validation': {
            'valid_rows': len(result.rows),
            'errors': [],
        },
    }


@router.get('/{batch_id}')
async def get_batch(
    batch_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Get batch job details.
    
    Args:
        batch_id: Batch job ID
        
    Returns:
        Batch job details
    """
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={'error': '无效的 batch_id 格式'},
        )
    
    batch = await BatchService.get_batch(db, batch_uuid)
    
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={'error': '批次不存在'},
        )
    
    # Calculate task counts from actual tasks
    task_counts = {
        'pending': 0,
        'queued': 0,
        'running': 0,
        'success': 0,
        'warning': 0,
        'failed': 0,
    }
    
    if batch.tasks:
        for task in batch.tasks:
            status = task.status
            if status == 'pending':
                task_counts['pending'] += 1
            elif status == 'queued':
                task_counts['queued'] += 1
            elif status == 'running':
                task_counts['running'] += 1
            elif status == 'success':
                task_counts['success'] += 1
            elif status == 'failed':
                task_counts['failed'] += 1
            # Note: warning status doesn't exist in BatchTaskStatus, but we keep it for future compatibility
    
    return {
        'batch_id': str(batch.batch_id),
        'status': batch.status,
        'total_count': batch.total_count,
        'task_counts': task_counts,
        'pending_count': batch.pending_count,
        'running_count': batch.running_count,
        'success_count': batch.success_count,
        'failed_count': batch.failed_count,
        'concurrency': batch.concurrency,
        'source_filename': batch.source_filename,
        'created_at': batch.created_at.isoformat() if batch.created_at else None,
        'started_at': batch.started_at.isoformat() if batch.started_at else None,
        'completed_at': batch.completed_at.isoformat() if batch.completed_at else None,
        'updated_at': batch.updated_at.isoformat() if batch.updated_at else None,
    }


def _serialize_task(task) -> dict:
    """Serialize a batch task using strict whitelist DTO.
    Only returns scalar fields needed by page and CSV.
    Never returns input_data, output_data, or ORM objects.
    """
    import json

    errors = []

    def safe_str(value, field_name=''):
        """Convert value to string safely."""
        if value is None:
            return None
        try:
            return str(value)
        except Exception as e:
            errors.append(f"{field_name}: {e}")
            return None

    def safe_int(value, field_name='', default=0):
        """Convert value to int safely."""
        if value is None:
            return default
        try:
            return int(value)
        except Exception as e:
            errors.append(f"{field_name}: {e}")
            return default

    def safe_datetime(value, field_name=''):
        """Convert datetime to ISO string safely."""
        if value is None:
            return None
        try:
            if hasattr(value, 'isoformat'):
                return value.isoformat()
            return str(value)
        except Exception as e:
            errors.append(f"{field_name}: {e}")
            return None

    def safe_extract_final_video_url(t):
        """Extract final_video_url: prefer DB field, fallback to output_data."""
        # Priority 1: DB field
        if t.final_video_url:
            return str(t.final_video_url)
        # Priority 2: output_data (internal only, never returned in response)
        try:
            od = t.output_data
            if od is None:
                return None
            if isinstance(od, str):
                try:
                    od = json.loads(od)
                except (json.JSONDecodeError, ValueError):
                    return None
            if isinstance(od, dict):
                url = od.get('final_video_url')
                if url and isinstance(url, str):
                    return url
        except Exception:
            pass
        return None

    def safe_extract_input_field(t, field, default=''):
        """Extract field from input_data (internal only, never returned in response)."""
        try:
            if t.input_data and isinstance(t.input_data, dict):
                val = t.input_data.get(field, default)
                if val is None:
                    return default
                return str(val)
        except Exception:
            pass
        return default

    # Build whitelist DTO - only scalar fields
    result = {}

    # task_id (required)
    try:
        result['task_id'] = safe_str(t.task_id if (t := task) else None, 'task_id')
    except Exception as e:
        errors.append(f"task_id: {e}")
        result['task_id'] = None

    # batch_id
    try:
        result['batch_id'] = safe_str(task.batch_id, 'batch_id')
    except Exception as e:
        errors.append(f"batch_id: {e}")
        result['batch_id'] = None

    # script_id - prefer DB field, fallback to input_data
    try:
        script_id = getattr(task, 'script_id', None)
        if not script_id:
            script_id = safe_extract_input_field(task, 'script_id')
        result['script_id'] = safe_str(script_id, 'script_id') or None
    except Exception as e:
        errors.append(f"script_id: {e}")
        result['script_id'] = None

    # title - prefer DB field, fallback to input_data
    try:
        title = getattr(task, 'title', None)
        if not title:
            title = safe_extract_input_field(task, 'title')
        result['title'] = safe_str(title, 'title') or None
    except Exception as e:
        errors.append(f"title: {e}")
        result['title'] = None

    # script_text - prefer DB field, fallback to input_data
    try:
        script_text = getattr(task, 'script_text', None)
        if not script_text:
            script_text = safe_extract_input_field(task, 'script_text')
        result['script_text'] = safe_str(script_text, 'script_text') or None
    except Exception as e:
        errors.append(f"script_text: {e}")
        result['script_text'] = None

    # status (required)
    try:
        status = task.status
        if hasattr(status, 'value'):
            status = status.value
        result['status'] = str(status)
    except Exception as e:
        errors.append(f"status: {e}")
        result['status'] = 'unknown'

    # final_video_url
    try:
        result['final_video_url'] = safe_extract_final_video_url(task)
    except Exception as e:
        errors.append(f"final_video_url: {e}")
        result['final_video_url'] = None

    # warning
    try:
        result['warning'] = safe_str(getattr(task, 'warning', None), 'warning')
    except Exception as e:
        errors.append(f"warning: {e}")
        result['warning'] = None

    # error_code
    try:
        result['error_code'] = safe_str(getattr(task, 'error_code', None), 'error_code')
    except Exception as e:
        errors.append(f"error_code: {e}")
        result['error_code'] = None

    # error_message
    try:
        result['error_message'] = safe_str(getattr(task, 'error_message', None), 'error_message')
    except Exception as e:
        errors.append(f"error_message: {e}")
        result['error_message'] = None

    # retry_count
    try:
        result['retry_count'] = safe_int(getattr(task, 'retry_count', 0), 'retry_count')
    except Exception as e:
        errors.append(f"retry_count: {e}")
        result['retry_count'] = 0

    # run_id
    try:
        result['run_id'] = safe_str(getattr(task, 'run_id', None), 'run_id')
    except Exception as e:
        errors.append(f"run_id: {e}")
        result['run_id'] = None

    # async_task_id
    try:
        result['async_task_id'] = safe_str(getattr(task, 'async_task_id', None), 'async_task_id')
    except Exception as e:
        errors.append(f"async_task_id: {e}")
        result['async_task_id'] = None

    # created_at
    try:
        result['created_at'] = safe_datetime(getattr(task, 'created_at', None), 'created_at')
    except Exception as e:
        errors.append(f"created_at: {e}")
        result['created_at'] = None

    # started_at
    try:
        result['started_at'] = safe_datetime(getattr(task, 'started_at', None), 'started_at')
    except Exception as e:
        errors.append(f"started_at: {e}")
        result['started_at'] = None

    # completed_at
    try:
        result['completed_at'] = safe_datetime(getattr(task, 'completed_at', None), 'completed_at')
    except Exception as e:
        errors.append(f"completed_at: {e}")
        result['completed_at'] = None

    # updated_at
    try:
        result['updated_at'] = safe_datetime(getattr(task, 'updated_at', None), 'updated_at')
    except Exception as e:
        errors.append(f"updated_at: {e}")
        result['updated_at'] = None

    # serialization_error
    result['serialization_error'] = '; '.join(errors) if errors else None

    return result


@router.get('/{batch_id}/tasks')
async def get_batch_tasks(
    batch_id: str,
    status: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
):
    """Get tasks for a batch job.
    
    Args:
        batch_id: Batch job ID
        status: Optional status filter
        page: Page number
        page_size: Page size
        
    Returns:
        Task list
    """
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={'error': '无效的 batch_id 格式'},
        )
    
    # Check batch exists
    batch = await BatchService.get_batch(db, batch_uuid)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={'error': '批次不存在'},
        )
    
    # Get tasks
    tasks, total_count = await BatchService.get_batch_tasks(
        db=db,
        batch_id=batch_uuid,
        status=status,
        page=page,
        page_size=page_size,
    )
    
    # Sync native async status for tasks with async_task_id
    completed_count = 0
    try:
        from api.async_task_service import get_async_task_service
        from main import service as graph_service
        async_task_service = get_async_task_service(graph_service)
        
        for task in tasks:
            if task.async_task_id and task.status in [BatchTaskStatus.QUEUED, BatchTaskStatus.RUNNING]:
                try:
                    old_status = task.status
                    # Poll native async status and update task
                    await async_task_service.poll_task_status(db, task)
                    # If task just completed (success or failed), count it
                    if old_status in [BatchTaskStatus.QUEUED, BatchTaskStatus.RUNNING] and \
                       task.status in [BatchTaskStatus.SUCCESS, BatchTaskStatus.FAILED]:
                        completed_count += 1
                except Exception as e:
                    logger.warning(f"Failed to sync task {task.task_id} status: {e}")
                    # Continue even if sync fails
    except Exception as e:
        logger.warning(f"Async task sync unavailable, skipping: {e}")
        # Continue without sync - task list still works with DB state
    
    # If any tasks completed, try to submit next pending tasks (concurrency refill)
    if completed_count > 0:
        try:
            await _refill_batch_slots(db, batch_uuid)
        except Exception as e:
            logger.warning(f"Failed to refill batch slots: {e}")
    
    # Serialize tasks with per-task error handling
    serialized_tasks = []
    for task in tasks:
        try:
            serialized_tasks.append(_serialize_task(task))
        except Exception as e:
            logger.error(f"Failed to serialize task {task.task_id}: {e}")
            # Return a safe fallback for this task so the list doesn't 500
            # Uses whitelist DTO - no input_data/output_data
            try:
                fallback_task_id = str(task.task_id) if task.task_id else 'unknown'
            except Exception:
                fallback_task_id = 'unknown'
            try:
                fallback_batch_id = str(task.batch_id) if task.batch_id else str(batch_id)
            except Exception:
                fallback_batch_id = str(batch_id)
            try:
                fallback_status = str(task.status) if task.status else 'unknown'
            except Exception:
                fallback_status = 'unknown'
            
            serialized_tasks.append({
                'task_id': fallback_task_id,
                'batch_id': fallback_batch_id,
                'script_id': None,
                'title': None,
                'script_text': None,
                'status': fallback_status,
                'final_video_url': None,
                'warning': None,
                'error_code': None,
                'error_message': None,
                'retry_count': 0,
                'run_id': None,
                'async_task_id': None,
                'created_at': None,
                'started_at': None,
                'completed_at': None,
                'updated_at': None,
                'serialization_error': str(e),
            })

    return {
        'batch_id': batch_id,
        'tasks': serialized_tasks,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': (total_count + page_size - 1) // page_size,
        },
    }


@router.post('/{batch_id}/start')
async def start_batch(
    batch_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Start executing a batch job.
    
    Args:
        batch_id: Batch job ID
        
    Returns:
        Batch execution result
    """
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={'error': '无效的 batch_id 格式'},
        )
    
    # Check batch exists
    batch = await BatchService.get_batch(db, batch_uuid)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={'error': '批次不存在'},
        )
    
    # Get graph service from app state
    from main import service
    executor = BatchExecutor(service)
    
    try:
        result = await executor.start_batch(db, batch_uuid)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={'error': str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={'error': f'启动批次失败: {str(e)}'},
        )


@router.post('/{batch_id}/tasks/{task_id}/retry', status_code=202)
async def retry_task(
    batch_id: str,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed task using native async task system.
    
    This endpoint submits the task to the native async task system and returns immediately.
    The task will be executed by the platform's async task runtime.
    
    Args:
        batch_id: Batch job ID
        task_id: Task ID to retry
        
    Returns:
        Task retry result (HTTP 202 Accepted)
    """
    try:
        batch_uuid = uuid.UUID(batch_id)
        task_uuid = uuid.UUID(task_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={'error': '无效的 ID 格式'},
        )
    
    # Check batch exists
    batch = await BatchService.get_batch(db, batch_uuid)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={'error': '批次不存在'},
        )
    
    # Get graph service from app state
    from main import service
    executor = BatchExecutor(service)
    
    try:
        result = await executor.retry_task(
            db, batch_uuid, task_uuid
        )
        return result
    except ValueError as e:
        error_msg = str(e)
        # Return 409 for status conflicts
        if "status" in error_msg and "only failed tasks can be retried" in error_msg:
            raise HTTPException(
                status_code=409,
                detail={'error': error_msg},
            )
        raise HTTPException(
            status_code=400,
            detail={'error': error_msg},
        )
    except Exception as e:
        logger.error(f"Failed to retry task {task_id}: {e}")
        raise HTTPException(
            status_code=500,
            detail={'error': f'重试失败: {str(e)}'},
        )


@router.post('/{batch_id}/retry-failed', status_code=202)
async def retry_failed_tasks(
    batch_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry all failed tasks in a batch using native async task system.
    
    This endpoint queues failed tasks for execution using the native async task
    system and returns immediately. The tasks will be executed in the background
    by the platform's async task runtime.
    
    Args:
        batch_id: Batch job ID
        db: Database session
        
    Returns:
        Retry result (HTTP 202 Accepted)
    """
    try:
        batch_uuid = uuid.UUID(batch_id)
    except ValueError:
        raise HTTPException(
            status_code=400,
            detail={'error': '无效的 batch_id 格式'},
        )
    
    # Check batch exists
    batch = await BatchService.get_batch(db, batch_uuid)
    if not batch:
        raise HTTPException(
            status_code=404,
            detail={'error': '批次不存在'},
        )
    
    # Get graph service from app state
    from main import service
    executor = BatchExecutor(service)
    
    try:
        result = await executor.retry_failed(db, batch_uuid)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={'error': str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={'error': f'重试失败任务失败: {str(e)}'},
        )


@router.post('/recover-stuck')
async def recover_stuck_tasks(
    db: AsyncSession = Depends(get_db_session),
):
    """Recover tasks stuck in running state.
    
    Returns:
        Recovery result
    """
    # Get graph service from app state
    from main import service
    executor = BatchExecutor(service)
    
    try:
        result = await executor.recover_stuck_tasks(db)
        return result
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={'error': f'恢复卡住任务失败: {str(e)}'},
        )



@router.get('')
async def list_batches(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db_session),
):
    """List batch jobs.
    
    Args:
        page: Page number
        page_size: Page size
        
    Returns:
        Batch list
    """
    batches, total_count = await BatchService.list_batches(
        db=db,
        page=page,
        page_size=page_size,
    )
    
    return {
        'batches': [
            {
                'batch_id': str(batch.batch_id),
                'status': batch.status,
                'total_count': batch.total_count,
                'success_count': batch.success_count,
                'failed_count': batch.failed_count,
                'concurrency': batch.concurrency,
                'source_filename': batch.source_filename,
                'created_at': batch.created_at.isoformat() if batch.created_at else None,
            }
            for batch in batches
        ],
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total_count': total_count,
            'total_pages': (total_count + page_size - 1) // page_size,
        },
    }
