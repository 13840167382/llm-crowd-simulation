"""实验维护任务 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, TimestampMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

maintenance_job_type_enum = build_postgres_enum(MaintenanceJobType, name="maintenance_job_type")
maintenance_job_status_enum = build_postgres_enum(MaintenanceJobStatus, name="maintenance_job_status")


class ExperimentMaintenanceJob(Base, BigIntPrimaryKeyMixin, TimestampMixin):
    """实验维护任务队列表。"""

    __tablename__ = "experiment_maintenance_jobs"
    __table_args__ = (
        Index("idx_experiment_maintenance_jobs_status_created_at", "status", "created_at"),
        Index("idx_experiment_maintenance_jobs_experiment_id_job_type", "experiment_id", "job_type"),
        {"comment": "实验维护任务队列表"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    job_type: Mapped[MaintenanceJobType] = mapped_column(
        maintenance_job_type_enum,
        nullable=False,
        comment="任务类型",
    )
    status: Mapped[MaintenanceJobStatus] = mapped_column(
        maintenance_job_status_enum,
        nullable=False,
        comment="任务状态",
    )
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0, comment="已尝试次数")
    payload_json: Mapped[dict[str, Any]] = mapped_column(
        "payload",
        JSONB,
        nullable=False,
        default=dict,
        comment="任务入队参数",
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(
        "result",
        JSONB,
        nullable=False,
        default=dict,
        comment="任务执行结果",
    )
    error_message: Mapped[str | None] = mapped_column(String(1000), nullable=True, comment="最近一次失败信息")
    scheduled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="计划执行时间",
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="开始执行时间",
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
        comment="执行结束时间",
    )
