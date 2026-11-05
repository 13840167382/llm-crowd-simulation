"""创建实验归档包表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260420_0017"
down_revision = "20260414_0016"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """执行升级迁移。"""

    op.create_table(
        "experiment_archives",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.String(length=64), nullable=False, comment="原实验对外编号"),
        sa.Column("experiment_name", sa.String(length=128), nullable=False, comment="原实验名称"),
        sa.Column("source_status", sa.String(length=32), nullable=False, comment="归档时实验状态"),
        sa.Column("archived_at_ms", sa.BigInteger(), nullable=False, comment="归档时间，毫秒"),
        sa.Column("archive_version", sa.String(length=32), nullable=False, comment="归档结构版本"),
        sa.Column(
            "archive_payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="自包含归档包",
        ),
        sa.Column(
            "summary",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="轻量归档摘要",
        ),
        sa.Column(
            "raw_runtime_retained",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
            comment="归档后是否仍保留原始运行明细",
        ),
        sa.Column("archive_location", sa.Text(), nullable=True, comment="外部归档位置"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.UniqueConstraint("experiment_id", name="uk_experiment_archives_experiment_id"),
        comment="实验归档包",
    )
    op.create_index(
        "idx_experiment_archives_archived_at_ms",
        "experiment_archives",
        ["archived_at_ms"],
        unique=False,
    )
    op.create_index(
        "idx_experiment_archives_source_status",
        "experiment_archives",
        ["source_status"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    op.drop_index("idx_experiment_archives_source_status", table_name="experiment_archives")
    op.drop_index("idx_experiment_archives_archived_at_ms", table_name="experiment_archives")
    op.drop_table("experiment_archives")
