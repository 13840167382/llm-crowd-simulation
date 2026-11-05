"""为 experiments 表增加 agent_id_prefix 字段。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260411_0003"
down_revision = "20260411_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "experiments",
        sa.Column("agent_id_prefix", sa.String(length=32), nullable=True, comment="出生点生成时使用的 agent_id 前缀"),
    )


def downgrade() -> None:
    op.drop_column("experiments", "agent_id_prefix")
