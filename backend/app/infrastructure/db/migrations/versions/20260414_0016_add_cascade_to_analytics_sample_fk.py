"""为新增分析表补充实验外键级联删除。"""

from __future__ import annotations

from alembic import op

revision = "20260414_0016"
down_revision = "20260414_0015"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移。"""

    op.drop_constraint(
        "fk_agent_metrics_1m_experiment_id_experiments",
        "agent_metrics_1m",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_agent_metrics_1m_experiment_id_experiments",
        "agent_metrics_1m",
        "experiments",
        ["experiment_id"],
        ["id"],
        ondelete="CASCADE",
    )

    op.drop_constraint(
        "fk_trajectory_points_sampled_1s_experiment_id_experiments",
        "trajectory_points_sampled_1s",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_trajectory_points_sampled_1s_experiment_id_experiments",
        "trajectory_points_sampled_1s",
        "experiments",
        ["experiment_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    op.drop_constraint(
        "fk_trajectory_points_sampled_1s_experiment_id_experiments",
        "trajectory_points_sampled_1s",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_trajectory_points_sampled_1s_experiment_id_experiments",
        "trajectory_points_sampled_1s",
        "experiments",
        ["experiment_id"],
        ["id"],
    )

    op.drop_constraint(
        "fk_agent_metrics_1m_experiment_id_experiments",
        "agent_metrics_1m",
        type_="foreignkey",
    )
    op.create_foreign_key(
        "fk_agent_metrics_1m_experiment_id_experiments",
        "agent_metrics_1m",
        "experiments",
        ["experiment_id"],
        ["id"],
    )
