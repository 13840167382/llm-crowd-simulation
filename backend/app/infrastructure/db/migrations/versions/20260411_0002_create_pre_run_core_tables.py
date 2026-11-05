"""创建实验启动前核心数据表。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "20260411_0002"
down_revision = "20260411_0001"
branch_labels = None
depends_on = None

zone_type_enum = postgresql.ENUM(
    "passage",
    "plaza",
    "exhibit",
    "rest",
    "commercial",
    "service",
    "exit_final",
    "other",
    name="zone_type",
    create_type=False,
)

exit_type_enum = postgresql.ENUM(
    "portal",
    "ramp",
    "elevator",
    "stair",
    name="exit_type",
    create_type=False,
)

distribution_mode_enum = postgresql.ENUM(
    "zone_weighted",
    "random_entry",
    "point_spread",
    "zone_fixed",
    name="distribution_mode",
    create_type=False,
)


def upgrade() -> None:
    """执行升级迁移。"""

    bind = op.get_bind()
    zone_type_enum.create(bind, checkfirst=True)
    exit_type_enum.create(bind, checkfirst=True)
    distribution_mode_enum.create(bind, checkfirst=True)

    op.create_table(
        "crowd_type_templates",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("type_id", sa.String(length=32), nullable=False, comment="对外模板标识"),
        sa.Column("display_name", sa.String(length=32), nullable=False, comment="显示名称"),
        sa.Column("color_hex", sa.CHAR(length=7), nullable=False, comment="颜色值"),
        sa.Column("is_builtin", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="是否系统内置"),
        sa.Column("base_speed", sa.Numeric(4, 2), nullable=False, comment="基础速度"),
        sa.Column("social_distance", sa.Numeric(4, 2), nullable=False, comment="社交距离"),
        sa.Column("tolerance_duration_ms", sa.Integer(), nullable=False, comment="容忍时长，毫秒"),
        sa.Column(
            "attraction_weights",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="功能区吸引权重",
        ),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="扩展字段",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.UniqueConstraint("type_id", name="uk_crowd_type_templates_type_id"),
        comment="全局人群类型模板定义",
    )
    op.create_index(
        "idx_crowd_type_templates_builtin",
        "crowd_type_templates",
        ["is_builtin"],
        unique=False,
    )

    op.create_table(
        "experiment_zones",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("zone_id", sa.String(length=64), nullable=False, comment="对外区域编号"),
        sa.Column("zone_name", sa.String(length=64), nullable=False, comment="区域名称"),
        sa.Column("zone_type", zone_type_enum, nullable=False, comment="区域类型"),
        sa.Column("min_x", sa.Numeric(10, 3), nullable=False, comment="边界最小 X"),
        sa.Column("min_z", sa.Numeric(10, 3), nullable=False, comment="边界最小 Z"),
        sa.Column("max_x", sa.Numeric(10, 3), nullable=False, comment="边界最大 X"),
        sa.Column("max_z", sa.Numeric(10, 3), nullable=False, comment="边界最大 Z"),
        sa.Column("capacity", sa.Integer(), nullable=False, comment="区域容量"),
        sa.Column("is_entry_zone", sa.Boolean(), nullable=False, server_default=sa.text("false"), comment="是否入口区域"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="扩展字段",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint("experiment_id", "zone_id", name="uk_experiment_zones_experiment_id_zone_id"),
        comment="实验拓扑中的区域定义",
    )
    op.create_index(
        "idx_experiment_zones_experiment_id_zone_type",
        "experiment_zones",
        ["experiment_id", "zone_type"],
        unique=False,
    )
    op.create_index(
        "idx_experiment_zones_experiment_id_is_entry_zone",
        "experiment_zones",
        ["experiment_id", "is_entry_zone"],
        unique=False,
    )

    op.create_table(
        "experiment_exits",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("exit_id", sa.String(length=64), nullable=False, comment="对外出口编号"),
        sa.Column("source_zone_id", sa.String(length=64), nullable=False, comment="源区域编号"),
        sa.Column("target_zone_id", sa.String(length=64), nullable=False, comment="目标区域编号"),
        sa.Column("exit_type", exit_type_enum, nullable=False, comment="出口类型"),
        sa.Column("position_x", sa.Numeric(10, 3), nullable=False, comment="出口 X 坐标"),
        sa.Column("position_z", sa.Numeric(10, 3), nullable=False, comment="出口 Z 坐标"),
        sa.Column("pass_capacity_per_sec", sa.Integer(), nullable=False, comment="每秒通行能力"),
        sa.Column(
            "metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
            comment="扩展字段",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.ForeignKeyConstraint(
            ["experiment_id", "source_zone_id"],
            ["experiment_zones.experiment_id", "experiment_zones.zone_id"],
            name="fk_experiment_exits_source_zone",
        ),
        sa.ForeignKeyConstraint(
            ["experiment_id", "target_zone_id"],
            ["experiment_zones.experiment_id", "experiment_zones.zone_id"],
            name="fk_experiment_exits_target_zone",
        ),
        sa.UniqueConstraint("experiment_id", "exit_id", name="uk_experiment_exits_experiment_id_exit_id"),
        comment="实验拓扑中的出口关系",
    )
    op.create_index(
        "idx_experiment_exits_experiment_id_source_zone_id",
        "experiment_exits",
        ["experiment_id", "source_zone_id"],
        unique=False,
    )
    op.create_index(
        "idx_experiment_exits_experiment_id_target_zone_id",
        "experiment_exits",
        ["experiment_id", "target_zone_id"],
        unique=False,
    )

    op.create_table(
        "experiment_allocations",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("type_id", sa.String(length=32), nullable=False, comment="模板标识"),
        sa.Column("count", sa.Integer(), nullable=False, comment="分配数量"),
        sa.Column("distribution_mode", distribution_mode_enum, nullable=False, comment="分配模式"),
        sa.Column(
            "distribution_config",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="分配参数",
        ),
        sa.Column(
            "template_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="模板快照",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="更新时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint(
            "experiment_id",
            "type_id",
            name="uk_experiment_allocations_experiment_id_type_id",
        ),
        comment="实验人群分配方案与模板快照",
    )
    op.create_index(
        "idx_experiment_allocations_experiment_id_distribution_mode",
        "experiment_allocations",
        ["experiment_id", "distribution_mode"],
        unique=False,
    )

    op.create_table(
        "experiment_agents",
        sa.Column("id", sa.BigInteger(), sa.Identity(start=1), primary_key=True, nullable=False, comment="内部主键"),
        sa.Column("experiment_id", sa.BigInteger(), nullable=False, comment="所属实验主键"),
        sa.Column("agent_id", sa.String(length=64), nullable=False, comment="对外 agent 编号"),
        sa.Column("crowd_type", sa.String(length=32), nullable=False, comment="人群类型"),
        sa.Column("spawn_zone_id", sa.String(length=64), nullable=False, comment="初始区域编号"),
        sa.Column("spawn_x", sa.Numeric(10, 3), nullable=False, comment="初始 X 坐标"),
        sa.Column("spawn_z", sa.Numeric(10, 3), nullable=False, comment="初始 Z 坐标"),
        sa.Column("rotation_y", sa.Numeric(6, 2), nullable=True, comment="初始朝向"),
        sa.Column("spawn_order", sa.Integer(), nullable=False, comment="生成顺序"),
        sa.Column("generation_seed", sa.Integer(), nullable=True, comment="生成种子"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now(), comment="创建时间"),
        sa.ForeignKeyConstraint(["experiment_id"], ["experiments.id"]),
        sa.UniqueConstraint("experiment_id", "agent_id", name="uk_experiment_agents_experiment_id_agent_id"),
        comment="实验出生点生成结果",
    )
    op.create_index(
        "idx_experiment_agents_experiment_id_crowd_type",
        "experiment_agents",
        ["experiment_id", "crowd_type"],
        unique=False,
    )
    op.create_index(
        "idx_experiment_agents_experiment_id_spawn_zone_id",
        "experiment_agents",
        ["experiment_id", "spawn_zone_id"],
        unique=False,
    )


def downgrade() -> None:
    """执行回滚迁移。"""

    bind = op.get_bind()

    op.drop_index("idx_experiment_agents_experiment_id_spawn_zone_id", table_name="experiment_agents")
    op.drop_index("idx_experiment_agents_experiment_id_crowd_type", table_name="experiment_agents")
    op.drop_table("experiment_agents")

    op.drop_index(
        "idx_experiment_allocations_experiment_id_distribution_mode",
        table_name="experiment_allocations",
    )
    op.drop_table("experiment_allocations")

    op.drop_index("idx_experiment_exits_experiment_id_target_zone_id", table_name="experiment_exits")
    op.drop_index("idx_experiment_exits_experiment_id_source_zone_id", table_name="experiment_exits")
    op.drop_table("experiment_exits")

    op.drop_index("idx_experiment_zones_experiment_id_is_entry_zone", table_name="experiment_zones")
    op.drop_index("idx_experiment_zones_experiment_id_zone_type", table_name="experiment_zones")
    op.drop_table("experiment_zones")

    op.drop_index("idx_crowd_type_templates_builtin", table_name="crowd_type_templates")
    op.drop_table("crowd_type_templates")

    distribution_mode_enum.drop(bind, checkfirst=True)
    exit_type_enum.drop(bind, checkfirst=True)
    zone_type_enum.drop(bind, checkfirst=True)
