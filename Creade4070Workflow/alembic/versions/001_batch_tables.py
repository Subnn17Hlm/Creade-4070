"""create batch tables

Revision ID: 001_batch_tables
Revises: 
Create Date: 2024-01-01 00:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import UUID, JSONB


# revision identifiers, used by Alembic.
revision: str = '001_batch_tables'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create batch_jobs table
    op.create_table(
        'batch_jobs',
        sa.Column('batch_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('status', sa.String(20), nullable=False, default='created'),
        sa.Column('total_count', sa.Integer(), nullable=False, default=0),
        sa.Column('pending_count', sa.Integer(), nullable=False, default=0),
        sa.Column('running_count', sa.Integer(), nullable=False, default=0),
        sa.Column('success_count', sa.Integer(), nullable=False, default=0),
        sa.Column('failed_count', sa.Integer(), nullable=False, default=0),
        sa.Column('concurrency', sa.Integer(), nullable=False, default=2),
        sa.Column('idempotency_key', sa.String(255), unique=True, nullable=True),
        sa.Column('source_filename', sa.String(255), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    
    # Create index for status queries
    op.create_index('ix_batch_jobs_status', 'batch_jobs', ['status'])
    op.create_index('ix_batch_jobs_created_at', 'batch_jobs', ['created_at'])
    
    # Create batch_tasks table
    op.create_table(
        'batch_tasks',
        sa.Column('task_id', UUID(as_uuid=True), primary_key=True),
        sa.Column('batch_id', UUID(as_uuid=True), sa.ForeignKey('batch_jobs.batch_id', ondelete='CASCADE'), nullable=False),
        sa.Column('row_number', sa.Integer(), nullable=False),
        sa.Column('external_task_id', sa.String(255), nullable=False),
        sa.Column('run_id', UUID(as_uuid=True), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, default='pending'),
        sa.Column('input_data', JSONB(), nullable=False),
        sa.Column('output_data', JSONB(), nullable=True),
        sa.Column('final_video_url', sa.Text(), nullable=True),
        sa.Column('error_code', sa.String(50), nullable=True),
        sa.Column('error_message', sa.Text(), nullable=True),
        sa.Column('retry_count', sa.Integer(), nullable=False, default=0),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('started_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('completed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    
    # Create constraints and indexes for batch_tasks
    op.create_unique_constraint('uq_batch_tasks_batch_row', 'batch_tasks', ['batch_id', 'row_number'])
    op.create_unique_constraint('uq_batch_tasks_batch_external', 'batch_tasks', ['batch_id', 'external_task_id'])
    op.create_index('ix_batch_tasks_batch_id', 'batch_tasks', ['batch_id'])
    op.create_index('ix_batch_tasks_status', 'batch_tasks', ['status'])
    op.create_index('ix_batch_tasks_run_id', 'batch_tasks', ['run_id'])


def downgrade() -> None:
    op.drop_table('batch_tasks')
    op.drop_table('batch_jobs')
