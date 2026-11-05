"""新增人物分钟聚合表与秒级采样轨迹表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260414_0015"
down_revision = "20260413_0014"
branch_labels = None
depends_on = None

agent_state_enum = postgresql.ENUM(
    "walking",
    "queuing",
    "idle",
    "transitioning",
    name="agent_state",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    agent_state_enum.create(bind, checkfirst=True)

    op.create_table(
        "agent_metrics_1m",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="主键 ID"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="实验主键 ID"),
        sa.Column("bucket_start_ms", sa.BigInteger(), nullable=False, comment="分钟聚合桶起始时间(毫秒)"),
        sa.Column("crowd_type", sa.String(length=32), nullable=True, comment="人群类型过滤，为空表示全量"),
        sa.Column("total_count", sa.Integer(), nullable=False, comment="该桶对应的人物总数"),
        sa.Column("active_count", sa.Integer(), nullable=False, comment="该桶内仍活跃的人物数"),
        sa.Column("exited_count", sa.Integer(), nullable=False, comment="该桶内已退出的人物数"),
        sa.Column("avg_speed", sa.Numeric(10, 2), nullable=False, comment="该桶内平均速度"),
        sa.Column("avg_stay_ms", sa.Integer(), nullable=True, comment="该桶内平均停留时长(毫秒)"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        comment="实验分钟级人物概览聚合表",
    )
    op.create_index(
        "idx_agent_metrics_1m_experiment_id_bucket_start_ms",
        "agent_metrics_1m",
        ["experiment_id", "bucket_start_ms"],
        unique=False,
    )
    op.create_index(
        "uk_agent_metrics_1m_all_crowd",
        "agent_metrics_1m",
        ["experiment_id", "bucket_start_ms"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NULL"),
    )
    op.create_index(
        "uk_agent_metrics_1m_by_crowd",
        "agent_metrics_1m",
        ["experiment_id", "bucket_start_ms", "crowd_type"],
        unique=True,
        postgresql_where=sa.text("crowd_type IS NOT NULL"),
    )

    op.create_table(
        "trajectory_points_sampled_1s",
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="实验主键 ID"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, comment="agent 标识"),
        sa.Column("simulation_time_ms", sa.BigInteger(), nullable=False, comment="保留用于查询的采样点时间(毫秒)"),
        sa.Column("crowd_type", sa.String(length=32), nullable=False, comment="人群类型"),
        sa.Column("zone_id", sa.String(length=64), nullable=False, comment="所在区域 ID"),
        sa.Column("position_x", sa.Numeric(10, 3), nullable=False, comment="X 坐标"),
        sa.Column("position_z", sa.Numeric(10, 3), nullable=False, comment="Z 坐标"),
        sa.Column("speed", sa.Numeric(5, 2), nullable=False, comment="速度"),
        sa.Column("state", agent_state_enum, nullable=False, comment="人物状态"),
        sa.Column("target_exit_id", sa.String(length=64), nullable=True, comment="目标出口 ID"),
        sa.Column("is_queuing", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="是否排队中"),
        sa.Column("sample_interval_ms", sa.Integer(), nullable=False, comment="采样点代表的时间间隔(毫秒)"),
        sa.Column("source_batch_id", sa.String(length=64), nullable=True, comment="来源轨迹批次 ID"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="入库时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("experiment_id", "agent_id", "simulation_time_ms", name="pk_trajectory_points_sampled_1s"),
        comment="面向查询的秒级采样轨迹点",
    )
    op.create_index(
        "idx_traj_points_sampled_1s_exp_time",
        "trajectory_points_sampled_1s",
        ["experiment_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_sampled_1s_exp_agent_time",
        "trajectory_points_sampled_1s",
        ["experiment_id", "agent_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_sampled_1s_exp_type_time",
        "trajectory_points_sampled_1s",
        ["experiment_id", "crowd_type", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_sampled_1s_exp_zone_time",
        "trajectory_points_sampled_1s",
        ["experiment_id", "zone_id", "simulation_time_ms"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    op.drop_index("idx_traj_points_sampled_1s_exp_zone_time", table_name="trajectory_points_sampled_1s")
    op.drop_index("idx_traj_points_sampled_1s_exp_type_time", table_name="trajectory_points_sampled_1s")
    op.drop_index("idx_traj_points_sampled_1s_exp_agent_time", table_name="trajectory_points_sampled_1s")
    op.drop_index("idx_traj_points_sampled_1s_exp_time", table_name="trajectory_points_sampled_1s")
    op.drop_table("trajectory_points_sampled_1s")

    op.drop_index("uk_agent_metrics_1m_by_crowd", table_name="agent_metrics_1m")
    op.drop_index("uk_agent_metrics_1m_all_crowd", table_name="agent_metrics_1m")
    op.drop_index("idx_agent_metrics_1m_experiment_id_bucket_start_ms", table_name="agent_metrics_1m")
    op.drop_table("agent_metrics_1m")
