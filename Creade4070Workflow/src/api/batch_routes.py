"""Batch API routes."""
import uuid
from typing import Optional

from fastapi import APIRouter, UploadFile, File, Form, Header, HTTPException, Query, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database.db import get_db_session
from storage.database.batch_models import BatchJob, BatchTask
from api.batch_csv import validate_csv, MAX_BATCH_SIZE
from api.batch_service import BatchService
from api.batch_executor import BatchExecutor

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
        return JSONResponse(
            status_code=400,
            content={
                'error': 'CSV 校验失败',
                'errors': [e.to_dict() for e in result.errors],
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
        raise HTTPException(
            status_code=500,
            detail={'error': f'创建批次失败: {str(e)}'},
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
    
    return {
        'batch_id': str(batch.batch_id),
        'status': batch.status,
        'total_count': batch.total_count,
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


@router.post('/{batch_id}/tasks/{task_id}/retry')
async def retry_task(
    batch_id: str,
    task_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry a failed task.
    
    Args:
        batch_id: Batch job ID
        task_id: Task ID to retry
        
    Returns:
        Task retry result
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
        result = await executor.retry_task(db, batch_uuid, task_uuid)
        return result
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={'error': str(e)},
        )
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={'error': f'重试任务失败: {str(e)}'},
        )


@router.post('/{batch_id}/retry-failed')
async def retry_failed_tasks(
    batch_id: str,
    db: AsyncSession = Depends(get_db_session),
):
    """Retry all failed tasks in a batch.
    
    Args:
        batch_id: Batch job ID
        
    Returns:
        Retry result
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
        result = await executor.retry_failed_tasks(db, batch_uuid)
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
