"""实验分配与出生点 ORM 模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import DistributionMode
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, CreatedAtMixin, TimestampMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

distribution_mode_enum = build_postgres_enum(DistributionMode, name="distribution_mode")


class ExperimentAllocation(Base, BigIntPrimaryKeyMixin, TimestampMixin):
    """实验人群分配配置。"""

    __tablename__ = "experiment_allocations"
    __table_args__ = (
        UniqueConstraint(
            "experiment_id",
            "type_id",
            name="uk_experiment_allocations_experiment_id_type_id",
        ),
        Index(
            "idx_experiment_allocations_experiment_id_distribution_mode",
            "experiment_id",
            "distribution_mode",
        ),
        {"comment": "实验人群分配方案与模板快照"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    type_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="模板标识")
    count: Mapped[int] = mapped_column(Integer, nullable=False, comment="分配数量")
    distribution_mode: Mapped[DistributionMode] = mapped_column(
        distribution_mode_enum,
        nullable=False,
        comment="分配模式",
    )
    distribution_config: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="分配参数",
    )
    template_snapshot: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="模板快照",
    )


class ExperimentAgent(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """出生点生成结果。"""

    __tablename__ = "experiment_agents"
    __table_args__ = (
        UniqueConstraint("experiment_id", "agent_id", name="uk_experiment_agents_experiment_id_agent_id"),
        Index("idx_experiment_agents_experiment_id_crowd_type", "experiment_id", "crowd_type"),
        Index("idx_experiment_agents_experiment_id_spawn_zone_id", "experiment_id", "spawn_zone_id"),
        {"comment": "实验出生点生成结果"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    agent_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外 agent 编号")
    crowd_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="人群类型")
    spawn_zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="初始区域编号")
    spawn_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="初始 X 坐标")
    spawn_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="初始 Z 坐标")
    rotation_y: Mapped[float | None] = mapped_column(
        Numeric(6, 2),
        nullable=True,
        comment="初始朝向",
    )
    spawn_order: Mapped[int] = mapped_column(Integer, nullable=False, comment="生成顺序")
    generation_seed: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="生成种子")
