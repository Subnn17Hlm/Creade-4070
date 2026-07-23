"""Batch API routes."""
import uuid
import logging
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database.db import get_db_session
from storage.database.batch_models import BatchJob, BatchTask
from api.batch_csv import validate_csv, MAX_BATCH_SIZE
from api.batch_service import BatchService
from api.batch_executor import BatchExecutor

logger = logging.getLogger(__name__)
router = APIRouter(prefix='/api/batches', tags=['batches'])


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
    
    return {
        'batch_id': batch_id,
        'tasks': [
            {
                'task_id': str(task.task_id),
                'row_number': task.row_number,
                'external_task_id': task.external_task_id,
                'run_id': str(task.run_id) if task.run_id else None,
                'status': task.status,
                'script_id': task.input_data.get('script_id', '') if task.input_data else '',
                'script_text': task.input_data.get('script_text', '') if task.input_data else '',
                'title': task.input_data.get('title', '') if task.input_data else '',
                'input_data': task.input_data,
                'output_data': task.output_data,
                'final_video_url': task.final_video_url,
                'error_code': task.error_code,
                'error_message': task.error_message,
                'retry_count': task.retry_count,
                'created_at': task.created_at.isoformat() if task.created_at else None,
                'started_at': task.started_at.isoformat() if task.started_at else None,
                'completed_at': task.completed_at.isoformat() if task.completed_at else None,
            }
            for task in tasks
        ],
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
