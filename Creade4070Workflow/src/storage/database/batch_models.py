"""
批量任务数据模型
================
定义 batch_jobs 和 batch_tasks 表的 SQLAlchemy 模型。
"""

import uuid
from datetime import datetime
from typing import Optional, Dict, Any
from enum import Enum

from sqlalchemy import (
    Column, String, Integer, DateTime, ForeignKey, Index, Text, JSON,
    UniqueConstraint
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship, DeclarativeBase


class Base(DeclarativeBase):
    """SQLAlchemy 2.0 声明式基类"""
    pass


class BatchJobStatus(str, Enum):
    """批次任务状态"""
    CREATED = "created"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_FAILED = "partial_failed"
    FAILED = "failed"


class BatchTaskStatus(str, Enum):
    """批次任务项状态"""
    PENDING = "pending"
    QUEUED = "queued"  # 已提交到异步队列，等待执行
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class BatchJob(Base):
    """批次任务表"""
    __tablename__ = "batch_jobs"

    batch_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    status = Column(String(20), nullable=False, default="created", index=True)
    total_count = Column(Integer, nullable=False, default=0)
    pending_count = Column(Integer, nullable=False, default=0)
    running_count = Column(Integer, nullable=False, default=0)
    success_count = Column(Integer, nullable=False, default=0)
    failed_count = Column(Integer, nullable=False, default=0)
    concurrency = Column(Integer, nullable=False, default=2)
    idempotency_key = Column(String(255), unique=True, nullable=True, index=True)
    source_filename = Column(String(255), nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    tasks = relationship("BatchTask", back_populates="batch", cascade="all, delete-orphan")

    def to_dict(self) -> Dict[str, Any]:
        """转换为字典"""
        return {
            "batch_id": str(self.batch_id),
            "status": self.status,
            "total_count": self.total_count,
            "pending_count": self.pending_count,
            "running_count": self.running_count,
            "success_count": self.success_count,
            "failed_count": self.failed_count,
            "concurrency": self.concurrency,
            "source_filename": self.source_filename,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class BatchTask(Base):
    """批次任务项表"""
    __tablename__ = "batch_tasks"

    task_id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    batch_id = Column(UUID(as_uuid=True), ForeignKey("batch_jobs.batch_id", ondelete="CASCADE"), nullable=False, index=True)
    row_number = Column(Integer, nullable=False)
    external_task_id = Column(String(255), nullable=False)
    run_id = Column(UUID(as_uuid=True), nullable=True, index=True)
    async_task_id = Column(String(255), nullable=True, index=True)  # 原生异步任务 ID
    status = Column(String(20), nullable=False, default="pending")
    input_data = Column(JSON, nullable=False)
    output_data = Column(JSON, nullable=True)
    final_video_url = Column(Text, nullable=True)
    warning = Column(Text, nullable=True)  # 降级警告信息
    error_code = Column(String(50), nullable=True)
    error_message = Column(Text, nullable=True)
    retry_count = Column(Integer, nullable=False, default=0)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    # 关系
    batch = relationship("BatchJob", back_populates="tasks")

    # 约束
    __table_args__ = (
        UniqueConstraint("batch_id", "row_number", name="uq_batch_task_row"),
        UniqueConstraint("batch_id", "external_task_id", name="uq_batch_task_external_id"),
        Index("ix_batch_tasks_status", "status"),
    )

    def to_dict(self, include_sensitive: bool = False) -> Dict[str, Any]:
        """转换为字典"""
        result = {
            "task_id": str(self.task_id),
            "batch_id": str(self.batch_id),
            "row_number": self.row_number,
            "external_task_id": self.external_task_id,
            "run_id": str(self.run_id) if self.run_id else None,
            "async_task_id": self.async_task_id,
            "status": self.status,
            "input_data": self.input_data,
            "final_video_url": self.final_video_url,
            "warning": self.warning,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
        if include_sensitive:
            result["output_data"] = self.output_data
        return result


__all__ = ["Base", "BatchJob", "BatchTask"]
