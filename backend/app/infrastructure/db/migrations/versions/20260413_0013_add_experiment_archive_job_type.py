"""添加 experiment_archive 维护任务类型。"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "20260413_0013"
down_revision = "20260413_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """为维护任务枚举增加实验归档类型。"""

    op.execute("ALTER TYPE maintenance_job_type ADD VALUE IF NOT EXISTS 'experiment_archive'")


def downgrade() -> None:
    """PostgreSQL 枚举值不安全删除，回滚时保持现状。"""

