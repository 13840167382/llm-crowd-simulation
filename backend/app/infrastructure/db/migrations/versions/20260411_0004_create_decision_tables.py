"""创建决策相关数据表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260411_0004"
down_revision = "20260411_0003"
branch_labels = None
depends_on = None

decision_source_enum = postgresql.ENUM(
    "llm",
    "fallback",
    "algorithm",
    "none",
    name="decision_source",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    decision_source_enum.create(bind, checkfirst=True)

    op.create_table(
        "decision_batches",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("batch_id", sa.String(length=64), nullable=False, comment="对外批次标识"),
        sa.Column("simulation_time_ms", sa.Integer(), nullable=False, comment="模拟时间，毫秒"),
        sa.Column("requested_count", sa.Integer(), nullable=False, comment="请求中的 agent 总数"),
        sa.Column("success_count", sa.Integer(), nullable=False, comment="成功决策数量"),
        sa.Column("fallback_count", sa.Integer(), nullable=False, comment="LLM fallback 数量"),
        sa.Column("processing_duration_ms", sa.Integer(), nullable=False, comment="处理耗时，毫秒"),
        sa.Column("status", sa.String(length=16), nullable=False, comment="批次处理状态"),
        sa.Column(
            "response_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="用于幂等回放的响应快照",
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False, comment="处理完成时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint("experiment_id", "batch_id", name="uk_decision_batches_experiment_id_batch_id"),
        comment="实验决策批次记录",
    )
    op.create_index(
        "idx_decision_batches_experiment_id_simulation_time_ms",
        "decision_batches",
        ["experiment_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_decision_batches_experiment_id_status",
        "decision_batches",
        ["experiment_id", "status"],
        unique=False,
    )

    op.create_table(
        "agent_decisions",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("decision_batch_id", sa.BigInteger(), nullable=False, comment="所属决策批次主键"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, comment="对外 agent 标识"),
        sa.Column("simulation_time_ms", sa.Integer(), nullable=False, comment="模拟时间，毫秒"),
        sa.Column("crowd_type", sa.String(length=32), nullable=False, comment="人群类型"),
        sa.Column("current_zone_id", sa.String(length=64), nullable=False, comment="当前区域标识"),
        sa.Column("stay_duration_ms", sa.Integer(), nullable=False, comment="区域停留时长，毫秒"),
        sa.Column("patience_ratio", sa.Numeric(5, 4), nullable=False, comment="耐心消耗比例"),
        sa.Column("algorithm_recommendation", sa.String(length=64), nullable=True, comment="算法推荐出口"),
        sa.Column(
            "available_exits_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
            comment="候选出口列表快照",
        ),
        sa.Column("dilemma_summary", sa.String(length=512), nullable=False, comment="困境摘要"),
        sa.Column("chosen_exit_id", sa.String(length=64), nullable=True, comment="最终选择出口"),
        sa.Column("decision_source", decision_source_enum, nullable=False, comment="决策来源"),
        sa.Column("reasoning", sa.String(length=1024), nullable=True, comment="决策理由"),
        sa.Column("confidence", sa.Numeric(5, 4), nullable=True, comment="决策置信度"),
        sa.Column("was_overridden", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="是否覆盖算法推荐"),
        sa.Column("llm_latency_ms", sa.Integer(), nullable=True, comment="LLM 调用耗时，毫秒"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(["decision_batch_id"], ["decision_batches.id"]),
        sa.UniqueConstraint("decision_batch_id", "agent_id", name="uk_agent_decisions_decision_batch_id_agent_id"),
        comment="单个 agent 的决策明细",
    )
    op.create_index(
        "idx_agent_decisions_experiment_id_agent_id_simulation_time_ms",
        "agent_decisions",
        ["experiment_id", "agent_id", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_agent_decisions_experiment_id_crowd_type_simulation_time_ms",
        "agent_decisions",
        ["experiment_id", "crowd_type", "simulation_time_ms"],
        unique=False,
    )
    op.create_index(
        "idx_agent_decisions_experiment_id_decision_source",
        "agent_decisions",
        ["experiment_id", "decision_source"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()

    op.drop_index("idx_agent_decisions_experiment_id_decision_source", table_name="agent_decisions")
    op.drop_index(
        "idx_agent_decisions_experiment_id_crowd_type_simulation_time_ms",
        table_name="agent_decisions",
    )
    op.drop_index(
        "idx_agent_decisions_experiment_id_agent_id_simulation_time_ms",
        table_name="agent_decisions",
    )
    op.drop_table("agent_decisions")

    op.drop_index("idx_decision_batches_experiment_id_status", table_name="decision_batches")
    op.drop_index(
        "idx_decision_batches_experiment_id_simulation_time_ms",
        table_name="decision_batches",
    )
    op.drop_table("decision_batches")

    decision_source_enum.drop(bind, checkfirst=True)
