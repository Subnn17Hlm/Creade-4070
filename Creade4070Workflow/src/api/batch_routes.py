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
    
    # Submit next tasks
    from src.api.async_task_service import get_async_task_service
    async_task_service = get_async_task_service()
    
    submitted = 0
    for task in pending_tasks:
        try:
            # Atomically claim the task using a separate session with row lock
            async with get_async_sessionmaker()() as claim_db:
                async with claim_db.begin():
                    claim_result = await claim_db.execute(
                        select(BatchTask)
                        .where(BatchTask.task_id == task.task_id)
                        .with_for_update()
                    )
                    locked_task = claim_result.scalar_one_or_none()
                    if not locked_task or locked_task.status != BatchTaskStatus.PENDING:
                        continue  # Already claimed by another poll
                    locked_task.status = BatchTaskStatus.QUEUED
                    run_id = uuid.uuid4()
                    locked_task.run_id = run_id
                    locked_task.started_at = datetime.utcnow()
            
            # Submit to native async system
            await async_task_service.submit_task(
                db=db,
                task=task,
                deadline_sec=1800,
            )
            submitted += 1
        except Exception as e:
            logger.error(f"Failed to submit task {task.task_id} during refill: {e}")
            # Mark task as failed
            try:
                async with get_async_sessionmaker()() as fail_db:
                    async with fail_db.begin():
                        fail_result = await fail_db.execute(
                            select(BatchTask).where(BatchTask.task_id == task.task_id)
                        )
                        fail_task = fail_result.scalar_one_or_none()
                        if fail_task:
                            fail_task.status = BatchTaskStatus.FAILED
                            fail_task.error_code = "SUBMIT_ERROR"
                            fail_task.error_message = str(e)
                            fail_task.completed_at = datetime.utcnow()
            except Exception as inner_e:
                logger.error(f"Failed to mark task {task.task_id} as failed: {inner_e}")
            continue
    
    if submitted > 0:
        logger.info(f"Refilled batch {batch_id}: submitted {submitted} more tasks")


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


def _sanitize_json_value(obj):
    """Recursively sanitize a JSON value to ensure it's serializable.
    Converts UUID, datetime, Enum to strings. Handles nested dicts/lists.
    """
    if obj is None:
        return None
    if isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, dict):
        return {str(k): _sanitize_json_value(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize_json_value(item) for item in obj]
    # UUID
    if hasattr(obj, 'hex') and hasattr(obj, 'version'):
        return str(obj)
    # datetime
    if hasattr(obj, 'isoformat'):
        return obj.isoformat()
    # Enum
    if hasattr(obj, 'value'):
        return obj.value
    # Fallback
    return str(obj)


def _serialize_task(task) -> dict:
    """Safely serialize a batch task to dict. Handles null/missing fields."""
    import json

    def safe_output_data(od):
        if od is None:
            return None
        if isinstance(od, dict):
            return _sanitize_json_value(od)
        if isinstance(od, str):
            try:
                parsed = json.loads(od)
                return _sanitize_json_value(parsed)
            except (json.JSONDecodeError, ValueError):
                return {"_raw": od}
        return {"_raw": str(od)}

    def safe_input_data(t):
        """Sanitize input_data to ensure JSON serializable."""
        if t.input_data is None:
            return None
        if isinstance(t.input_data, dict):
            return _sanitize_json_value(t.input_data)
        if isinstance(t.input_data, str):
            try:
                parsed = json.loads(t.input_data)
                return _sanitize_json_value(parsed)
            except (json.JSONDecodeError, ValueError):
                return {"_raw": t.input_data}
        return {"_raw": str(t.input_data)}

    def safe_final_video_url(t):
        if t.final_video_url:
            return t.final_video_url
        od = safe_output_data(t.output_data)
        if od and isinstance(od, dict):
            return od.get("final_video_url") or None
        return None

    def safe_input_field(t, field, default=''):
        try:
            if t.input_data and isinstance(t.input_data, dict):
                val = t.input_data.get(field, default)
                return val if val is not None else default
        except Exception:
            pass
        return default

    def safe_status(t):
        """Get status value, handling Enum."""
        s = t.status
        if hasattr(s, 'value'):
            return s.value
        return str(s) if s else None

    return {
        'task_id': str(task.task_id),
        'batch_id': str(task.batch_id),
        'row_number': task.row_number,
        'external_task_id': task.external_task_id,
        'run_id': str(task.run_id) if task.run_id else None,
        'async_task_id': task.async_task_id if task.async_task_id else None,
        'status': safe_status(task),
        'script_id': safe_input_field(task, 'script_id'),
        'script_text': safe_input_field(task, 'script_text'),
        'title': safe_input_field(task, 'title'),
        'input_data': safe_input_data(task),
        'output_data': safe_output_data(task.output_data),
        'final_video_url': safe_final_video_url(task),
        'warning': getattr(task, 'warning', None),
        'error_code': task.error_code,
        'error_message': task.error_message,
        'retry_count': task.retry_count or 0,
        'created_at': task.created_at.isoformat() if task.created_at else None,
        'started_at': task.started_at.isoformat() if task.started_at else None,
        'completed_at': task.completed_at.isoformat() if task.completed_at else None,
        'updated_at': task.updated_at.isoformat() if getattr(task, 'updated_at', None) else None,
    }


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
    from src.api.async_task_service import AsyncTaskService
    async_task_service = AsyncTaskService()
    
    completed_count = 0
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
            serialized_tasks.append({
                'task_id': str(task.task_id),
                'batch_id': str(task.batch_id),
                'status': str(task.status) if task.status else 'unknown',
                'serialization_error': str(e),
                'row_number': task.row_number,
                'external_task_id': task.external_task_id,
                'run_id': str(task.run_id) if task.run_id else None,
                'async_task_id': None,
                'script_id': '',
                'script_text': '',
                'title': '',
                'input_data': None,
                'output_data': None,
                'final_video_url': None,
                'warning': None,
                'error_code': None,
                'error_message': None,
                'retry_count': task.retry_count or 0,
                'created_at': task.created_at.isoformat() if task.created_at else None,
                'started_at': None,
                'completed_at': None,
                'updated_at': None,
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
    
    # Get async task service
    from src.api.async_task_service import get_async_task_service
    async_task_service = get_async_task_service()
    
    try:
        result = await executor.retry_task(
            db, batch_uuid, task_uuid, async_task_service=async_task_service
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
    
    # Create async task service
    async_task_service = AsyncTaskService()
    
    try:
        result = await executor.retry_failed(db, batch_uuid, async_task_service)
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
