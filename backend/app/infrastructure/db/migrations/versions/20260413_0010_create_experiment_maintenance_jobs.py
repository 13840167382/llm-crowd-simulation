"""创建实验维护任务队列表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260413_0010"
down_revision = "20260413_0009"
branch_labels = None
depends_on = None

maintenance_job_type_enum = postgresql.ENUM(
    "runtime_data_cleanup",
    name="maintenance_job_type",
    create_type=False,
)

maintenance_job_status_enum = postgresql.ENUM(
    "pending",
    "running",
    "succeeded",
    "failed",
    name="maintenance_job_status",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    maintenance_job_type_enum.create(bind, checkfirst=True)
    maintenance_job_status_enum.create(bind, checkfirst=True)

    op.create_table(
        "experiment_maintenance_jobs",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("job_type", maintenance_job_type_enum, nullable=False, comment="任务类型"),
        sa.Column("status", maintenance_job_status_enum, nullable=False, comment="任务状态"),
        sa.Column("attempt_count", sa.Integer(), nullable=False, server_default=sa.text("0"), comment="已尝试次数"),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="任务入队参数",
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="任务执行结果",
        ),
        sa.Column("error_message", sa.String(length=1000), nullable=True, comment="最近一次失败信息"),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True, comment="计划执行时间"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True, comment="开始执行时间"),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True, comment="执行结束时间"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        comment="实验维护任务队列表",
    )
    op.create_index(
        "idx_experiment_maintenance_jobs_status_created_at",
        "experiment_maintenance_jobs",
        ["status", "created_at"],
        unique=False,
    )
    op.create_index(
        "idx_experiment_maintenance_jobs_experiment_id_job_type",
        "experiment_maintenance_jobs",
        ["experiment_id", "job_type"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()

    op.drop_index(
        "idx_experiment_maintenance_jobs_experiment_id_job_type",
        table_name="experiment_maintenance_jobs",
    )
    op.drop_index(
        "idx_experiment_maintenance_jobs_status_created_at",
        table_name="experiment_maintenance_jobs",
    )
    op.drop_table("experiment_maintenance_jobs")

    maintenance_job_status_enum.drop(bind, checkfirst=True)
    maintenance_job_type_enum.drop(bind, checkfirst=True)
