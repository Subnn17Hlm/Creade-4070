"""Add async_task_id and warning fields to batch_tasks

Revision ID: 002
Revises: 001
Create Date: 2026-01-21

"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = '002'
down_revision = '001_batch_tables'
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Add async_task_id and warning fields to batch_tasks table"""
    op.add_column('batch_tasks', sa.Column('async_task_id', sa.String(255), nullable=True))
    op.add_column('batch_tasks', sa.Column('warning', sa.Text(), nullable=True))
    
    # Create index on async_task_id for efficient lookups
    op.create_index('ix_batch_tasks_async_task_id', 'batch_tasks', ['async_task_id'])


def downgrade() -> None:
    """Remove async_task_id and warning fields from batch_tasks table"""
    op.drop_index('ix_batch_tasks_async_task_id', table_name='batch_tasks')
    op.drop_column('batch_tasks', 'warning')
    op.drop_column('batch_tasks', 'async_task_id')
