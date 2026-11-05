"""分析层聚合表与缓存表 ORM 模型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer, Numeric, String, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import HeatmapMetric, NormalizationMode
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, CreatedAtMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

heatmap_metric_enum = build_postgres_enum(HeatmapMetric, name="heatmap_metric")
normalization_mode_enum = build_postgres_enum(NormalizationMode, name="normalization_mode")


class ZoneMetric1m(Base, BigIntPrimaryKeyMixin):
    """实验分钟级区域统计聚合表。"""

    __tablename__ = "zone_metrics_1m"
    __table_args__ = (
        Index("idx_zone_metrics_1m_experiment_id_zone_id_bucket_start_ms", "experiment_id", "zone_id", "bucket_start_ms"),
        Index(
            "uk_zone_metrics_1m_all_crowd",
            "experiment_id",
            "bucket_start_ms",
            "zone_id",
            unique=True,
            postgresql_where=text("crowd_type IS NULL"),
        ),
        Index(
            "uk_zone_metrics_1m_by_crowd",
            "experiment_id",
            "bucket_start_ms",
            "zone_id",
            "crowd_type",
            unique=True,
            postgresql_where=text("crowd_type IS NOT NULL"),
        ),
        {"comment": "实验分钟级区域统计聚合表"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        comment="实验主键 ID",
    )
    bucket_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="分钟聚合桶起始时间(毫秒)")
    zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="区域 ID")
    crowd_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="人群类型过滤，为空表示全量")
    sample_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="参与统计的采样点数量")
    avg_count: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, comment="区域平均人数")
    peak_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="区域峰值人数")
    total_entries: Mapped[int] = mapped_column(Integer, nullable=False, comment="流入区域总次数")
    total_exits: Mapped[int] = mapped_column(Integer, nullable=False, comment="流出区域总次数")
    queue_peak_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="排队峰值人数")
    avg_stay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="平均停留时长(毫秒)")
    utilization_rate: Mapped[float | None] = mapped_column(Numeric(6, 4), nullable=True, comment="区域利用率")


class ExitMetric1m(Base, BigIntPrimaryKeyMixin):
    """实验分钟级出口统计聚合表。"""

    __tablename__ = "exit_metrics_1m"
    __table_args__ = (
        Index("idx_exit_metrics_1m_experiment_id_exit_id_bucket_start_ms", "experiment_id", "exit_id", "bucket_start_ms"),
        Index(
            "uk_exit_metrics_1m_all_crowd",
            "experiment_id",
            "bucket_start_ms",
            "exit_id",
            unique=True,
            postgresql_where=text("crowd_type IS NULL"),
        ),
        Index(
            "uk_exit_metrics_1m_by_crowd",
            "experiment_id",
            "bucket_start_ms",
            "exit_id",
            "crowd_type",
            unique=True,
            postgresql_where=text("crowd_type IS NOT NULL"),
        ),
        {"comment": "实验分钟级出口统计聚合表"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        comment="实验主键 ID",
    )
    bucket_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="分钟聚合桶起始时间(毫秒)")
    exit_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="出口 ID")
    crowd_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="人群类型过滤，为空表示全量")
    total_passages: Mapped[int] = mapped_column(Integer, nullable=False, comment="总通过次数")
    avg_queue_length: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True, comment="平均排队长度")
    max_queue_length: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="最大排队长度")
    avg_wait_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="平均等待时长(毫秒)")
    max_wait_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="最大等待时长(毫秒)")
    throughput_per_min: Mapped[float | None] = mapped_column(Numeric(10, 2), nullable=True, comment="每分钟吞吐量")


class DecisionMetric1m(Base, BigIntPrimaryKeyMixin):
    """实验分钟级决策统计聚合表。"""

    __tablename__ = "decision_metrics_1m"
    __table_args__ = (
        Index("idx_decision_metrics_1m_experiment_id_bucket_start_ms", "experiment_id", "bucket_start_ms"),
        Index(
            "uk_decision_metrics_1m_all_crowd",
            "experiment_id",
            "bucket_start_ms",
            unique=True,
            postgresql_where=text("crowd_type IS NULL"),
        ),
        Index(
            "uk_decision_metrics_1m_by_crowd",
            "experiment_id",
            "bucket_start_ms",
            "crowd_type",
            unique=True,
            postgresql_where=text("crowd_type IS NOT NULL"),
        ),
        {"comment": "实验分钟级决策统计聚合表"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        comment="实验主键 ID",
    )
    bucket_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="分钟聚合桶起始时间(毫秒)")
    crowd_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="人群类型过滤，为空表示全量")
    decided_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="已决策人数")
    llm_success_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="LLM 成功决策数")
    fallback_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="Fallback 决策数")
    override_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="算法推荐被覆盖次数")
    avg_llm_latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="平均 LLM 决策耗时(毫秒)")


class AgentMetric1m(Base, BigIntPrimaryKeyMixin):
    """实验分钟级人物概览聚合表。"""

    __tablename__ = "agent_metrics_1m"
    __table_args__ = (
        Index("idx_agent_metrics_1m_experiment_id_bucket_start_ms", "experiment_id", "bucket_start_ms"),
        Index(
            "uk_agent_metrics_1m_all_crowd",
            "experiment_id",
            "bucket_start_ms",
            unique=True,
            postgresql_where=text("crowd_type IS NULL"),
        ),
        Index(
            "uk_agent_metrics_1m_by_crowd",
            "experiment_id",
            "bucket_start_ms",
            "crowd_type",
            unique=True,
            postgresql_where=text("crowd_type IS NOT NULL"),
        ),
        {"comment": "实验分钟级人物概览聚合表"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id", ondelete="CASCADE"),
        nullable=False,
        comment="实验主键 ID",
    )
    bucket_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="分钟聚合桶起始时间(毫秒)")
    crowd_type: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="人群类型过滤，为空表示全量")
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="该桶对应的人物总数")
    active_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="该桶内仍活跃的人物数")
    exited_count: Mapped[int] = mapped_column(Integer, nullable=False, comment="该桶内已退出的人物数")
    avg_speed: Mapped[float] = mapped_column(Numeric(10, 2), nullable=False, comment="该桶内平均速度")
    avg_stay_ms: Mapped[int | None] = mapped_column(Integer, nullable=True, comment="该桶内平均停留时长(毫秒)")


class HeatmapCache(Base, CreatedAtMixin):
    """热力图结果缓存表。"""

    __tablename__ = "heatmap_cache"
    __table_args__ = (
        Index("idx_heatmap_cache_experiment_id_metric", "experiment_id", "metric"),
        Index("idx_heatmap_cache_experiment_id_created_at", "experiment_id", "created_at"),
        {"comment": "热力图结果缓存表"},
    )

    cache_key: Mapped[str] = mapped_column(String(64), primary_key=True, nullable=False, comment="缓存键")
    experiment_id: Mapped[int] = mapped_column(ForeignKey("experiments.id"), nullable=False, comment="实验主键 ID")
    metric: Mapped[HeatmapMetric] = mapped_column(heatmap_metric_enum, nullable=False, comment="热力图指标")
    time_start_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="时间窗口起始毫秒")
    time_end_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="时间窗口结束毫秒")
    crowd_type_filter: Mapped[str | None] = mapped_column(String(32), nullable=True, comment="人群类型过滤")
    zone_id_filter: Mapped[str | None] = mapped_column(String(64), nullable=True, comment="区域过滤")
    normalization: Mapped[NormalizationMode] = mapped_column(normalization_mode_enum, nullable=False, comment="归一化方式")
    grid_hash: Mapped[str] = mapped_column(String(64), nullable=False, comment="网格配置哈希")
    statistics_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        default=dict,
        comment="统计摘要 JSON",
    )
    cells: Mapped[list[float]] = mapped_column(
        postgresql.ARRAY(postgresql.REAL(asdecimal=False)),
        nullable=False,
        default=list,
        comment="热力图网格值数组",
    )
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, comment="缓存过期时间")
