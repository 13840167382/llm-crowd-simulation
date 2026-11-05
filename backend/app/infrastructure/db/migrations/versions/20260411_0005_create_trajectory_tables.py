"""创建轨迹相关数据表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260411_0005"
down_revision = "20260411_0004"
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
        "trajectory_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("batch_id", sa.String(length=64), nullable=False, comment="对外批次标识"),
        sa.Column("simulation_time_ms", sa.BigInteger(), nullable=False, comment="模拟时间，毫秒"),
        sa.Column("sample_interval_ms", sa.Integer(), nullable=False, comment="采样间隔，毫秒"),
        sa.Column("point_count", sa.Integer(), nullable=False, comment="提交点数"),
        sa.Column("accepted_count", sa.Integer(), nullable=False, comment="接收点数"),
        sa.Column("rejected_count", sa.Integer(), nullable=False, comment="拒绝点数"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint("experiment_id", "batch_id", name="uk_trajectory_batches_experiment_id_batch_id"),
        comment="轨迹批次写入日志",
    )
    op.create_index(
        "idx_traj_batches_exp_time",
        "trajectory_batches",
        ["experiment_id", "simulation_time_ms"],
        unique=False,
    )

    op.create_table(
        "trajectory_points",
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, comment="agent 标识"),
        sa.Column("simulation_time_ms", sa.BigInteger(), nullable=False, comment="模拟时间，毫秒"),
        sa.Column("crowd_type", sa.String(length=32), nullable=False, comment="人群类型"),
        sa.Column("zone_id", sa.String(length=64), nullable=False, comment="当前区域"),
        sa.Column("position_x", sa.Numeric(10, 3), nullable=False, comment="X 坐标"),
        sa.Column("position_z", sa.Numeric(10, 3), nullable=False, comment="Z 坐标"),
        sa.Column("speed", sa.Numeric(5, 2), nullable=False, comment="速度"),
        sa.Column("state", agent_state_enum, nullable=False, comment="行为状态"),
        sa.Column("target_exit_id", sa.String(length=64), nullable=True, comment="目标出口"),
        sa.Column("is_queuing", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="是否排队"),
        sa.Column("sample_interval_ms", sa.Integer(), nullable=False, comment="采样间隔，毫秒"),
        sa.Column("batch_id", sa.String(length=64), nullable=True, comment="来源批次标识"),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="写入时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.PrimaryKeyConstraint("experiment_id", "agent_id", "simulation_time_ms", name="pk_trajectory_points"),
        comment="轨迹采样点明细（TimescaleDB hypertable）",
    )
    op.create_index(
        "idx_traj_points_exp_time",
        "trajectory_points",
        ["experiment_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_exp_agent_time",
        "trajectory_points",
        ["experiment_id", "agent_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_exp_type_time",
        "trajectory_points",
        ["experiment_id", "crowd_type", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_traj_points_exp_zone_time",
        "trajectory_points",
        ["experiment_id", "zone_id", "simulation_time_ms"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()

    op.drop_index(
        "idx_traj_points_exp_zone_time",
        table_name="trajectory_points",
    )
    op.drop_index(
        "idx_traj_points_exp_type_time",
        table_name="trajectory_points",
    )
    op.drop_index(
        "idx_traj_points_exp_agent_time",
        table_name="trajectory_points",
    )
    op.drop_index(
        "idx_traj_points_exp_time",
        table_name="trajectory_points",
    )
    op.drop_table("trajectory_points")

    op.drop_index(
        "idx_traj_batches_exp_time",
        table_name="trajectory_batches",
    )
    op.drop_table("trajectory_batches")

    agent_state_enum.drop(bind, checkfirst=True)
