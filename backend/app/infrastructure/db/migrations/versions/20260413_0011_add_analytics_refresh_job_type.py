"""为维护任务类型增加 analytics_refresh。"""

from __future__ import annotations

from alembic import op

revision = "20260413_0011"
down_revision = "20260413_0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移。"""

    op.execute("ALTER TYPE maintenance_job_type ADD VALUE IF NOT EXISTS 'analytics_refresh'")


def downgrade() -> None:
    """执行回滚迁移。"""

    # PostgreSQL 不支持安全地直接删除 enum 取值，这里保持 no-op。
    return None
