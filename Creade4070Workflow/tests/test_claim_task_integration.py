"""
Integration test for claim_task_for_execution using real database.

This test verifies that the claim_task_for_execution function works correctly
with a real database, not just mocks.
"""
import pytest
import pytest_asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import patch, AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy import select

from storage.database.batch_models import Base, BatchJob, BatchTask, BatchTaskStatus, BatchJobStatus
from api.batch_executor import claim_task_for_execution


@pytest_asyncio.fixture
async def real_db_engine():
    """Create a real async database engine for testing."""
    import os
    import tempfile
    
    # Use a temporary file for the test database
    fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(fd)
    
    # Use SQLite for testing
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        echo=False,
    )
    
    # Create all tables
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    yield engine
    
    # Drop all tables and clean up
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    await engine.dispose()
    
    # Remove the test database file
    if os.path.exists(db_path):
        os.remove(db_path)


@pytest_asyncio.fixture
async def real_db_session(real_db_engine):
    """Create a real async database session for testing."""
    session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    async with session_factory() as session:
        yield session


@pytest.mark.asyncio
async def test_claim_task_with_real_database(real_db_session, real_db_engine):
    """
    Test that claim_task_for_execution works with a real database.
    
    This test:
    1. Creates a batch job with 6 PENDING tasks
    2. Calls claim_task_for_execution for 2 tasks
    3. Verifies that exactly 2 tasks are claimed (status=RUNNING)
    4. Verifies that the remaining 4 tasks are still PENDING
    """
    # Create a batch job
    batch_id = uuid.uuid4()
    batch = BatchJob(
        batch_id=batch_id,
        status=BatchJobStatus.CREATED,
        total_count=6,
        pending_count=6,
        running_count=0,
        success_count=0,
        failed_count=0,
        concurrency=2,
    )
    real_db_session.add(batch)
    
    # Create 6 PENDING tasks
    task_ids = []
    for i in range(6):
        task_id = uuid.uuid4()
        task_ids.append(task_id)
        task = BatchTask(
            task_id=task_id,
            batch_id=batch_id,
            row_number=i + 1,
            external_task_id=f"external_{i}",
            status=BatchTaskStatus.PENDING.value,
            input_data={"test": f"data_{i}"},
        )
        real_db_session.add(task)
    
    await real_db_session.commit()
    
    # Verify all tasks are PENDING
    result = await real_db_session.execute(
        select(BatchTask).where(BatchTask.batch_id == batch_id)
    )
    tasks = result.scalars().all()
    assert len(tasks) == 6
    for task in tasks:
        assert task.status == BatchTaskStatus.PENDING.value
    
    # Close the session to ensure all data is committed and visible to other connections
    await real_db_session.close()
    
    # Mock get_async_sessionmaker to return our real session factory
    session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    with patch('storage.database.db.get_async_sessionmaker', return_value=session_factory):
        # Claim first 2 tasks
        run_id_1 = uuid.uuid4()
        success_1, diagnostic_1 = await claim_task_for_execution(task_ids[0], run_id_1)
        assert success_1 is True, f"First claim failed: {diagnostic_1}"
        
        run_id_2 = uuid.uuid4()
        success_2, diagnostic_2 = await claim_task_for_execution(task_ids[1], run_id_2)
        assert success_2 is True, f"Second claim failed: {diagnostic_2}"
    
    # Create a new session for verification since the original session was closed
    verify_session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with verify_session_factory() as verify_session:
        # Verify that exactly 2 tasks are now RUNNING
        result = await verify_session.execute(
            select(BatchTask).where(BatchTask.batch_id == batch_id)
        )
        tasks = result.scalars().all()
        
        running_tasks = [t for t in tasks if t.status == BatchTaskStatus.RUNNING.value]
        pending_tasks = [t for t in tasks if t.status == BatchTaskStatus.PENDING.value]
        
        assert len(running_tasks) == 2, f"Expected 2 RUNNING tasks, got {len(running_tasks)}"
        assert len(pending_tasks) == 4, f"Expected 4 PENDING tasks, got {len(pending_tasks)}"
        
        # Verify the correct tasks are RUNNING
        running_task_ids = {t.task_id for t in running_tasks}
        assert task_ids[0] in running_task_ids
        assert task_ids[1] in running_task_ids
        
        # Verify run_id is set correctly
        for task in running_tasks:
            if task.task_id == task_ids[0]:
                assert task.run_id == run_id_1
            elif task.task_id == task_ids[1]:
                assert task.run_id == run_id_2


@pytest.mark.asyncio
async def test_claim_task_idempotent_with_real_database(real_db_session, real_db_engine):
    """
    Test that claiming the same task twice fails the second time.
    """
    # Create a batch job
    batch_id = uuid.uuid4()
    batch = BatchJob(
        batch_id=batch_id,
        status=BatchJobStatus.CREATED,
        total_count=1,
        pending_count=1,
        running_count=0,
        success_count=0,
        failed_count=0,
        concurrency=1,
    )
    real_db_session.add(batch)
    
    # Create 1 PENDING task
    task_id = uuid.uuid4()
    task = BatchTask(
        task_id=task_id,
        batch_id=batch_id,
        row_number=1,
        external_task_id="external_1",
        status=BatchTaskStatus.PENDING.value,
        input_data={"test": "data"},
    )
    real_db_session.add(task)
    
    await real_db_session.commit()
    
    # Close the session to ensure all data is committed and visible to other connections
    await real_db_session.close()
    
    # Mock get_async_sessionmaker to return our real session factory
    session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    with patch('storage.database.db.get_async_sessionmaker', return_value=session_factory):
        # Claim the task first time
        run_id_1 = uuid.uuid4()
        success_1, diagnostic_1 = await claim_task_for_execution(task_id, run_id_1)
        assert success_1 is True, f"First claim failed: {diagnostic_1}"
        
        # Try to claim the same task again with a different run_id
        run_id_2 = uuid.uuid4()
        success_2, diagnostic_2 = await claim_task_for_execution(task_id, run_id_2)
        assert success_2 is False, "Second claim should fail"
        assert diagnostic_2["update_rowcount"] == 0
    
    # Verify the task is still RUNNING with the first run_id
    verify_session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with verify_session_factory() as verify_session:
        result = await verify_session.execute(
            select(BatchTask).where(BatchTask.task_id == task_id)
        )
        task = result.scalar_one()
        assert task.status == BatchTaskStatus.RUNNING.value
        assert task.run_id == run_id_1


@pytest.mark.asyncio
async def test_claim_task_diagnostic_info_with_real_database(real_db_session, real_db_engine):
    """
    Test that claim_task_for_execution returns correct diagnostic information.
    """
    # Create a batch job
    batch_id = uuid.uuid4()
    batch = BatchJob(
        batch_id=batch_id,
        status=BatchJobStatus.CREATED,
        total_count=1,
        pending_count=1,
        running_count=0,
        success_count=0,
        failed_count=0,
        concurrency=1,
    )
    real_db_session.add(batch)
    
    # Create 1 PENDING task
    task_id = uuid.uuid4()
    task = BatchTask(
        task_id=task_id,
        batch_id=batch_id,
        row_number=1,
        external_task_id="external_1",
        status=BatchTaskStatus.PENDING.value,
        input_data={"test": "data"},
    )
    real_db_session.add(task)
    
    await real_db_session.commit()
    
    # Close the session to ensure all data is committed and visible to other connections
    await real_db_session.close()
    
    # Mock get_async_sessionmaker to return our real session factory
    session_factory = async_sessionmaker(
        real_db_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    
    with patch('storage.database.db.get_async_sessionmaker', return_value=session_factory):
        # Claim the task
        run_id = uuid.uuid4()
        success, diagnostic = await claim_task_for_execution(task_id, run_id)
        
        # Verify diagnostic information
        assert success is True
        assert diagnostic["task_id"] == str(task_id)
        assert diagnostic["task_id_type"] == "UUID"
        assert diagnostic["run_id"] == str(run_id)
        assert diagnostic["run_id_type"] == "UUID"
        assert diagnostic["actual_task_id"] == str(task_id)
        assert diagnostic["actual_batch_id"] == str(batch_id)
        assert diagnostic["actual_status"] == BatchTaskStatus.PENDING.value
        assert diagnostic["status_matches_pending"] is True
        assert diagnostic["update_rowcount"] == 1
