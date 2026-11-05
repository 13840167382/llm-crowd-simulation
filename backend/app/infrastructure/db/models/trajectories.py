"""轨迹写入与查询相关 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Index, Integer, Numeric, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import AgentState
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, CreatedAtMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

agent_state_enum = build_postgres_enum(AgentState, name="agent_state")


class TrajectoryBatch(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """轨迹批次入库记录。"""

    __tablename__ = "trajectory_batches"
    __table_args__ = (
        UniqueConstraint("experiment_id", "batch_id", name="uk_trajectory_batches_experiment_id_batch_id"),
        Index("idx_traj_batches_exp_time", "experiment_id", "simulation_time_ms"),
        {"comment": "轨迹批次入库记录"},
    )

    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False, comment="实验主键 ID")
    batch_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="轨迹批次 ID")
    simulation_time_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="轨迹批次对应的模拟时间(毫秒)")
    sample_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="批次采样间隔(毫秒)")
    point_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="批次总点数")
    accepted_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="成功写入点数")
    rejected_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="被去重点数")


class TrajectoryPoint(Base):
    """原始轨迹点明细表。"""

    __tablename__ = "trajectory_points"
    __table_args__ = (
        Index("idx_traj_points_exp_time", "experiment_id", "simulation_time_ms"),
        Index("idx_traj_points_exp_agent_time", "experiment_id", "agent_id", "simulation_time_ms"),
        Index("idx_traj_points_exp_type_time", "experiment_id", "crowd_type", "simulation_time_ms"),
        Index("idx_traj_points_exp_zone_time", "experiment_id", "zone_id", "simulation_time_ms"),
        {"comment": "原始轨迹点明细表(TimescaleDB hypertable)"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="实验主键 ID",
    )
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False, comment="agent 标识")
    simulation_time_ms: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        nullable=False,
        comment="轨迹点对应的模拟时间(毫秒)",
    )
    crowd_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="人群类型")
    zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所在区域 ID")
    position_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="X 坐标")
    position_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="Z 坐标")
    speed: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, comment="速度")
    state: Mapped[AgentState] = mapped_column(agent_state_enum, nullable=False, comment="人物状态")
    target_exit_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="目标出口 ID")
    is_queuing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否排队中")
    sample_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="原始上报采样间隔(毫秒)")
    batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源轨迹批次 ID")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="入库时间")


class TrajectoryPointSampled1s(Base):
    """面向查询的秒级采样轨迹点。"""

    __tablename__ = "trajectory_points_sampled_1s"
    __table_args__ = (
        Index("idx_traj_points_sampled_1s_exp_time", "experiment_id", "simulation_time_ms"),
        Index("idx_traj_points_sampled_1s_exp_agent_time", "experiment_id", "agent_id", "simulation_time_ms"),
        Index("idx_traj_points_sampled_1s_exp_type_time", "experiment_id", "crowd_type", "simulation_time_ms"),
        Index("idx_traj_points_sampled_1s_exp_zone_time", "experiment_id", "zone_id", "simulation_time_ms"),
        {"comment": "面向查询的秒级采样轨迹点"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        comment="实验主键 ID",
    )
    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False, comment="agent 标识")
    simulation_time_ms: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        nullable=False,
        comment="保留用于查询的采样点时间(毫秒)",
    )
    crowd_type: Mapped[str] = mapped_column(String(32), nullable=False, comment="人群类型")
    zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="所在区域 ID")
    position_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="X 坐标")
    position_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="Z 坐标")
    speed: Mapped[float] = mapped_column(Numeric(5, 2), nullable=False, comment="速度")
    state: Mapped[AgentState] = mapped_column(agent_state_enum, nullable=False, comment="人物状态")
    target_exit_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="目标出口 ID")
    is_queuing: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, comment="是否排队中")
    sample_interval_ms: Mapped[int] = mapped_column(Integer, nullable=False, comment="采样点代表的时间间隔(毫秒)")
    source_batch_id: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="来源轨迹批次 ID")
    ingested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, comment="入库时间")
