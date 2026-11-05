"""创建 experiments 表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260411_0001"
down_revision = None
branch_labels = None
depends_on = None

experiment_status_enum = postgresql.ENUM(
    "initialized",
    "configured",
    "running",
    "completed",
    "failed",
    name="experiment_status",
    create_type=False,
)

completion_reason_enum = postgresql.ENUM(
    "simulation_finished",
    "interrupted",
    "error",
    name="completion_reason",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    experiment_status_enum.create(bind, checkfirst=True)
    completion_reason_enum.create(bind, checkfirst=True)

    op.create_table(
        "experiments",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.String(length=64), nullable=False, comment="对外实验编号"),
        sa.Column("name", sa.String(length=128), nullable=False, comment="实验名称"),
        sa.Column("description", sa.Text(), nullable=True, comment="实验描述"),
        sa.Column("map_id", sa.String(length=64), nullable=True, comment="地图标识"),
        sa.Column("status", experiment_status_enum, nullable=False, comment="当前实验状态"),
        sa.Column("llm_model", sa.String(length=64), nullable=False, comment="模型名称"),
        sa.Column("llm_temperature", sa.Numeric(3, 2), nullable=False, comment="LLM 温度"),
        sa.Column("llm_max_tokens", sa.Integer(), nullable=False, comment="最大 token 数"),
        sa.Column("llm_timeout_ms", sa.Integer(), nullable=False, comment="LLM 超时时间，毫秒"),
        sa.Column("decision_batch_interval_ms", sa.Integer(), nullable=False, comment="决策批次周期，毫秒"),
        sa.Column("decision_batch_size", sa.Integer(), nullable=False, comment="单批决策数量"),
        sa.Column("fallback_to_algorithm", sa.Boolean(), nullable=False, comment="是否允许算法兜底"),
        sa.Column(
            "tags",
            postgresql.ARRAY(sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::text[]"),
            comment="实验标签",
        ),
        sa.Column("simulation_started_at_ms", sa.BigInteger(), nullable=True, comment="模拟启动时刻，毫秒"),
        sa.Column("simulation_completed_at_ms", sa.BigInteger(), nullable=True, comment="模拟结束时刻，毫秒"),
        sa.Column("actual_duration_ms", sa.Integer(), nullable=True, comment="实际运行时长，毫秒"),
        sa.Column("completion_reason", completion_reason_enum, nullable=True, comment="实验完成原因"),
        sa.Column("final_agent_count", sa.Integer(), nullable=True, comment="结束时 agent 数"),
        sa.Column(
            "summary_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="实验完成摘要",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.UniqueConstraint("experiment_id", name="uk_experiments_experiment_id"),
        comment="实验生命周期与运行配置",
    )
    op.create_index(
        "idx_experiments_status_created_at",
        "experiments",
        ["status", sa.text("created_at DESC")],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()
    op.drop_index("idx_experiments_status_created_at", table_name="experiments")
    op.drop_table("experiments")
    completion_reason_enum.drop(bind, checkfirst=True)
    experiment_status_enum.drop(bind, checkfirst=True)
