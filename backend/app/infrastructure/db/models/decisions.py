"""实验决策 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import DecisionSource
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, CreatedAtMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

decision_source_enum = build_postgres_enum(DecisionSource, name="decision_source")


class DecisionBatch(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """单个实验的决策批次记录。"""

    __tablename__ = "decision_batches"
    __table_args__ = (
        UniqueConstraint("experiment_id", "batch_id", name="uk_decision_batches_experiment_id_batch_id"),
        Index(
            "idx_decision_batches_experiment_id_simulation_time_ms",
            "experiment_id",
            "simulation_time_ms",
        ),
        Index("idx_decision_batches_experiment_id_status", "experiment_id", "status"),
        {"comment": "实验决策批次记录"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外批次标识")
    simulation_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="模拟时间，毫秒")
    requested_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="请求中的 agent 总数")
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="成功决策数量")
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="LLM fallback 数量")
    processing_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="处理耗时，毫秒")
    status: Mapped[str] = mapped_column(String(16), nullable=False, comment="批次处理状态")
    response_payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="用于幂等回放的响应快照",
    )
    processed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="处理完成时间",
    )


class AgentDecision(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """单个 agent 的决策结果记录。"""

    __tablename__ = "agent_decisions"
    __table_args__ = (
        UniqueConstraint("decision_batch_id", "agent_id", name="uk_agent_decisions_decision_batch_id_agent_id"),
        Index(
            "idx_agent_decisions_experiment_id_agent_id_simulation_time_ms",
            "experiment_id",
            "agent_id",
            "simulation_time_ms",
        ),
        Index(
            "idx_agent_decisions_experiment_id_crowd_type_simulation_time_ms",
            "experiment_id",
            "crowd_type",
            "simulation_time_ms",
        ),
        Index("idx_agent_decisions_experiment_id_decision_source", "experiment_id", "decision_source"),
        {"comment": "单个 agent 的决策明细"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    decision_batch_id: Mapped[int] = mapped_column(
        ForeignKey("decision_batches.id"),
        nullable=False,
        comment="所属决策批次主键",
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外 agent 标识")
    simulation_time_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="模拟时间，毫秒")
    crowd_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="人群类型")
    current_zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="当前区域标识")
    stay_duration_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="区域停留时长，毫秒")
    patience_ratio: Mapped[float] = mapped_column(
        Numeric(5, 4),
        nullable=False,
        comment="耐心消耗比例",
    )
    algorithm_recommendation: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        comment="算法推荐出口",
    )
    available_exits_json: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB,
        nullable=False,
        default=list,
        comment="候选出口列表快照",
    )
    dilemma_summary: Mapped[str] = mapped_column(String(512), nullable=False, comment="困境摘要")
    chosen_exit_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="最终选择出口")
    decision_source: Mapped[DecisionSource] = mapped_column(
        decision_source_enum,
        nullable=False,
        comment="决策来源",
    )
    reasoning: Mapped[str | None] = mapped_column(String(1024), nullable=True, comment="决策理由")
    confidence: Mapped[float | None] = mapped_column(
        Numeric(5, 4),
        nullable=True,
        comment="决策置信度",
    )
    was_overridden: Mapped[bool] = mapped_column(
        nullable=False,
        default=False,
        comment="是否覆盖算法推荐",
    )
    llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="LLM 调用耗时，毫秒")
