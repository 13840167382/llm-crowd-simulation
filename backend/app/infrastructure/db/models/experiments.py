"""实验 ORM 模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, Integer, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import CompletionReason, ExperimentStatus
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, TimestampMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

experiment_status_enum = build_postgres_enum(ExperimentStatus, name="experiment_status")
completion_reason_enum = build_postgres_enum(CompletionReason, name="completion_reason")


class Experiment(Base, BigIntPrimaryKeyMixin, TimestampMixin):
    """实验生命周期与运行配置。"""

    __tablename__ = "experiments"
    __table_args__ = (
        UniqueConstraint("experiment_id", name="uk_experiments_experiment_id"),
        Index("idx_experiments_status_created_at", "status", text("created_at DESC")),
        {"comment": "实验生命周期与运行配置"},
    )

    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外实验编号")
    name: Mapped[str] = mapped_column(String(128), nullable=False, comment="实验名称")
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="实验描述")
    map_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="地图标识")
    status: Mapped[ExperimentStatus] = mapped_column(
        experiment_status_enum,
        nullable=False,
        comment="当前实验状态",
    )
    llm_model: Mapped[str] = mapped_column(String(64), nullable=False, comment="模型名称")
    llm_temperature: Mapped[float] = mapped_column(
        Numeric(3, 2),
        nullable=False,
        comment="LLM 温度",
    )
    llm_max_tokens: Mapped[int] = mapped_column(Integer, nullable=False, comment="最大 token 数")
    llm_timeout_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="LLM 超时时间，毫秒")
    decision_batch_interval_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="决策批次周期，毫秒",
    )
    decision_batch_size: Mapped[int] = mapped_column(Integer, nullable=False, comment="单批决策数量")
    fallback_to_algorithm: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        comment="是否允许算法兜底",
    )
    tags: Mapped[list[str]] = mapped_column(
        ARRAY(Text()),
        nullable=False,
        server_default=text("'{}'::text[]"),
        comment="实验标签",
    )
    agent_id_prefix: Mapped[str | None] = mapped_column(
        String(32),
        nullable=True,
        comment="出生点生成时使用的 agent_id 前缀",
    )
    simulation_started_at_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="模拟启动时刻，毫秒",
    )
    simulation_completed_at_ms: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="模拟结束时刻，毫秒",
    )
    actual_duration_ms: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="实际运行时长，毫秒",
    )
    completion_reason: Mapped[CompletionReason | None] = mapped_column(
        completion_reason_enum,
        nullable=True,
        comment="实验完成原因",
    )
    final_agent_count: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="结束时 agent 数")
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="实验完成摘要",
    )
