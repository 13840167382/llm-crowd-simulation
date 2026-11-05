"""创建分析聚合与热力图缓存表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260413_0007"
down_revision = "20260411_0006"
branch_labels = None
depends_on = None

heatmap_metric_enum = postgresql.ENUM(
    "stay_duration",
    "passage_count",
    "peak_density",
    "queue_frequency",
    "avg_speed",
    name="heatmap_metric",
    create_type=False,
)

normalization_mode_enum = postgresql.ENUM(
    "none",
    "min_max",
    "percentile_99",
    "log",
    name="normalization_mode",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    heatmap_metric_enum.create(bind, checkfirst=True)
    normalization_mode_enum.create(bind, checkfirst=True)

    op.create_table(
        "zone_metrics_1m",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("bucket_start_ms", sa.BigInteger(), nullable=False, comment="分钟桶起点，毫秒"),
        sa.Column("zone_id", sa.String(length=64), nullable=False, comment="区域标识"),
        sa.Column("crowd_type", sa.String(length=32), nullable=True, comment="人群类型；为空表示全量"),
        sa.Column("sample_count", sa.Integer(), nullable=False, comment="采样点样本数"),
        sa.Column("avg_count", sa.Numeric(10, 2), nullable=False, comment="平均人数"),
        sa.Column("peak_count", sa.Integer(), nullable=False, comment="峰值人数"),
        sa.Column("total_entries", sa.Integer(), nullable=False, comment="总进入次数"),
        sa.Column("total_exits", sa.Integer(), nullable=False, comment="总离开次数"),
        sa.Column("queue_peak_count", sa.Integer(), nullable=False, comment="排队峰值人数"),
        sa.Column("avg_stay_ms", sa.Integer(), nullable=True, comment="平均停留时长，毫秒"),
        sa.Column("utilization_rate", sa.Numeric(6, 4), nullable=True, comment="区域利用率"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        comment="按分钟聚合的区域统计",
    )
    op.create_index(
        "idx_zone_metrics_1m_experiment_id_zone_id_bucket_start_ms",
        "zone_metrics_1m",
        ["experiment_id", "zone_id", "bucket_start_ms"],
        unique=False,
    )
    op.create_index(
        "uk_zone_metrics_1m_all_crowd",
        "zone_metrics_1m",
        ["experiment_id", "bucket_start_ms", "zone_id"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NULL"),
    )
    op.create_index(
        "uk_zone_metrics_1m_by_crowd",
        "zone_metrics_1m",
        ["experiment_id", "bucket_start_ms", "zone_id", "crowd_type"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NOT NULL"),
    )

    op.create_table(
        "exit_metrics_1m",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("bucket_start_ms", sa.BigInteger(), nullable=False, comment="分钟桶起点，毫秒"),
        sa.Column("exit_id", sa.String(length=64), nullable=False, comment="出口标识"),
        sa.Column("crowd_type", sa.String(length=32), nullable=True, comment="人群类型；为空表示全量"),
        sa.Column("total_passages", sa.Integer(), nullable=False, comment="总通行次数"),
        sa.Column("avg_queue_length", sa.Numeric(10, 2), nullable=True, comment="平均排队长度"),
        sa.Column("max_queue_length", sa.Integer(), nullable=True, comment="最大排队长度"),
        sa.Column("avg_wait_ms", sa.Integer(), nullable=True, comment="平均等待时长，毫秒"),
        sa.Column("max_wait_ms", sa.Integer(), nullable=True, comment="最大等待时长，毫秒"),
        sa.Column("throughput_per_min", sa.Numeric(10, 2), nullable=True, comment="每分钟吞吐量"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        comment="按分钟聚合的出口统计",
    )
    op.create_index(
        "idx_exit_metrics_1m_experiment_id_exit_id_bucket_start_ms",
        "exit_metrics_1m",
        ["experiment_id", "exit_id", "bucket_start_ms"],
        unique=False,
    )
    op.create_index(
        "uk_exit_metrics_1m_all_crowd",
        "exit_metrics_1m",
        ["experiment_id", "bucket_start_ms", "exit_id"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NULL"),
    )
    op.create_index(
        "uk_exit_metrics_1m_by_crowd",
        "exit_metrics_1m",
        ["experiment_id", "bucket_start_ms", "exit_id", "crowd_type"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NOT NULL"),
    )

    op.create_table(
        "decision_metrics_1m",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("bucket_start_ms", sa.BigInteger(), nullable=False, comment="分钟桶起点，毫秒"),
        sa.Column("crowd_type", sa.String(length=32), nullable=True, comment="人群类型；为空表示全量"),
        sa.Column("decided_count", sa.Integer(), nullable=False, comment="总决策次数"),
        sa.Column("llm_success_count", sa.Integer(), nullable=False, comment="LLM 成功次数"),
        sa.Column("fallback_count", sa.Integer(), nullable=False, comment="Fallback 次数"),
        sa.Column("override_count", sa.Integer(), nullable=False, comment="覆盖算法推荐次数"),
        sa.Column("avg_llm_latency_ms", sa.Integer(), nullable=True, comment="平均 LLM 延迟，毫秒"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        comment="按分钟聚合的决策统计",
    )
    op.create_index(
        "idx_decision_metrics_1m_experiment_id_bucket_start_ms",
        "decision_metrics_1m",
        ["experiment_id", "bucket_start_ms"],
        unique=False,
    )
    op.create_index(
        "uk_decision_metrics_1m_all_crowd",
        "decision_metrics_1m",
        ["experiment_id", "bucket_start_ms"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NULL"),
    )
    op.create_index(
        "uk_decision_metrics_1m_by_crowd",
        "decision_metrics_1m",
        ["experiment_id", "bucket_start_ms", "crowd_type"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NOT NULL"),
    )

    op.create_table(
        "heatmap_cache",
        sa.Column("cache_key", sa.String(length=64), primary_key=True, nullable=False, comment="基于请求参数生成的缓存键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("metric", heatmap_metric_enum, nullable=False, comment="热力图指标类型"),
        sa.Column("time_start_ms", sa.BigInteger(), nullable=False, comment="缓存命中的时间窗口起点"),
        sa.Column("time_end_ms", sa.BigInteger(), nullable=False, comment="缓存命中的时间窗口终点"),
        sa.Column("crowd_type_filter", sa.String(length=32), nullable=True, comment="人群类型过滤；为空表示不过滤"),
        sa.Column("zone_id_filter", sa.String(length=64), nullable=True, comment="区域过滤；为空表示不过滤"),
        sa.Column("normalization", normalization_mode_enum, nullable=False, comment="热力图归一化方式"),
        sa.Column("grid_hash", sa.String(length=64), nullable=False, comment="网格配置哈希"),
        sa.Column(
            "statistics_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="热力图统计摘要缓存",
        ),
        sa.Column(
            "cells",
            postgresql.ARRAY(postgresql.REAL(asdecimal=False)),
            nullable=False,
            server_default=sa.text("'{}'::real[]"),
            comment="按行优先排列的热力图格子值",
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True, comment="缓存过期时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        comment="热力图与对比热力图缓存",
    )
    op.create_index(
        "idx_heatmap_cache_experiment_id_metric",
        "heatmap_cache",
        ["experiment_id", "metric"],
        unique=False,
    )
    op.create_index(
        "idx_heatmap_cache_experiment_id_created_at",
        "heatmap_cache",
        ["experiment_id", "created_at"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()

    op.drop_index("idx_heatmap_cache_experiment_id_created_at", table_name="heatmap_cache")
    op.drop_index("idx_heatmap_cache_experiment_id_metric", table_name="heatmap_cache")
    op.drop_table("heatmap_cache")

    op.drop_index("uk_decision_metrics_1m_by_crowd", table_name="decision_metrics_1m")
    op.drop_index("uk_decision_metrics_1m_all_crowd", table_name="decision_metrics_1m")
    op.drop_index("idx_decision_metrics_1m_experiment_id_bucket_start_ms", table_name="decision_metrics_1m")
    op.drop_table("decision_metrics_1m")

    op.drop_index("uk_exit_metrics_1m_by_crowd", table_name="exit_metrics_1m")
    op.drop_index("uk_exit_metrics_1m_all_crowd", table_name="exit_metrics_1m")
    op.drop_index("idx_exit_metrics_1m_experiment_id_exit_id_bucket_start_ms", table_name="exit_metrics_1m")
    op.drop_table("exit_metrics_1m")

    op.drop_index("uk_zone_metrics_1m_by_crowd", table_name="zone_metrics_1m")
    op.drop_index("uk_zone_metrics_1m_all_crowd", table_name="zone_metrics_1m")
    op.drop_index("idx_zone_metrics_1m_experiment_id_zone_id_bucket_start_ms", table_name="zone_metrics_1m")
    op.drop_table("zone_metrics_1m")

    normalization_mode_enum.drop(bind, checkfirst=True)
    heatmap_metric_enum.drop(bind, checkfirst=True)
