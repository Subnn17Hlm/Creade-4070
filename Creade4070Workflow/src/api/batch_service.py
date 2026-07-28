"""Batch service for managing batch jobs and tasks."""
import uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import select, update, func
from sqlalchemy.ext.asyncio import AsyncSession

from storage.database.batch_models import (
    BatchJob, BatchTask, BatchJobStatus, BatchTaskStatus,
)


class BatchService:
    """Service for batch job operations."""
    
    @staticmethod
    async def create_batch(
        db: AsyncSession,
        rows: List[dict],
        concurrency: int = 2,
        idempotency_key: Optional[str] = None,
        source_filename: Optional[str] = None,
    ) -> BatchJob:
        """Create a new batch job with tasks.
        
        Args:
            db: Database session
            rows: List of parsed CSV rows
            concurrency: Concurrency level (1-4)
            idempotency_key: Optional idempotency key
            source_filename: Optional source filename
            
        Returns:
            Created BatchJob with tasks
        """
        # Check idempotency
        if idempotency_key:
            result = await db.execute(
                select(BatchJob).where(BatchJob.idempotency_key == idempotency_key)
            )
            existing = result.scalar_one_or_none()
            if existing:
                return existing
        
        # Create batch job
        batch_id = uuid.uuid4()
        batch = BatchJob(
            batch_id=batch_id,
            status=BatchJobStatus.CREATED,
            total_count=len(rows),
            pending_count=len(rows),
            running_count=0,
            success_count=0,
            failed_count=0,
            concurrency=concurrency,
            idempotency_key=idempotency_key,
            source_filename=source_filename,
        )
        db.add(batch)
        
        # Create tasks
        for row in rows:
            # Build input_data with all available fields
            input_data = {
                'script_text': row['script_text'],
            }
            # Persist stable batch_task_index for subtitle style rotation
            if 'batch_task_index' in row:
                input_data['batch_task_index'] = row['batch_task_index']
            # Add optional fields if present
            if row.get('script_id'):
                input_data['script_id'] = row['script_id']
            if row.get('title'):
                input_data['title'] = row['title']
            
            task = BatchTask(
                task_id=uuid.uuid4(),
                batch_id=batch_id,
                row_number=row['row_number'],
                external_task_id=row['task_id'],
                status=BatchTaskStatus.PENDING,
                input_data=input_data,
            )
            db.add(task)
        
        await db.commit()
        await db.refresh(batch)
        
        return batch
    
    @staticmethod
    async def get_batch(db: AsyncSession, batch_id: uuid.UUID) -> Optional[BatchJob]:
        """Get a batch job by ID.
        
        Args:
            db: Database session
            batch_id: Batch job ID
            
        Returns:
            BatchJob or None
        """
        from sqlalchemy.orm import selectinload
        result = await db.execute(
            select(BatchJob)
            .where(BatchJob.batch_id == batch_id)
            .options(selectinload(BatchJob.tasks))
        )
        return result.scalar_one_or_none()
    
    @staticmethod
    async def get_batch_tasks(
        db: AsyncSession,
        batch_id: uuid.UUID,
        status: Optional[str] = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[BatchTask], int]:
        """Get tasks for a batch job.
        
        Args:
            db: Database session
            batch_id: Batch job ID
            status: Optional status filter
            page: Page number (1-indexed)
            page_size: Page size
            
        Returns:
            Tuple of (tasks, total_count)
        """
        # Build query
        query = select(BatchTask).where(BatchTask.batch_id == batch_id)
        count_query = select(func.count()).select_from(BatchTask).where(BatchTask.batch_id == batch_id)
        
        if status:
            query = query.where(BatchTask.status == status)
            count_query = count_query.where(BatchTask.status == status)
        
        # Get total count
        total_result = await db.execute(count_query)
        total_count = total_result.scalar() or 0
        
        # Get paginated tasks
        query = query.order_by(BatchTask.row_number)
        query = query.offset((page - 1) * page_size).limit(page_size)
        
        result = await db.execute(query)
        tasks = list(result.scalars().all())
        
        return tasks, total_count
    
    @staticmethod
    async def list_batches(
        db: AsyncSession,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[List[BatchJob], int]:
        """List batch jobs.
        
        Args:
            db: Database session
            page: Page number (1-indexed)
            page_size: Page size
            
        Returns:
            Tuple of (batches, total_count)
        """
        # Get total count
        count_result = await db.execute(select(func.count()).select_from(BatchJob))
        total_count = count_result.scalar() or 0
        
        # Get paginated batches
        query = select(BatchJob).order_by(BatchJob.created_at.desc())
        query = query.offset((page - 1) * page_size).limit(page_size)
        
        result = await db.execute(query)
        batches = list(result.scalars().all())
        
        return batches, total_count
