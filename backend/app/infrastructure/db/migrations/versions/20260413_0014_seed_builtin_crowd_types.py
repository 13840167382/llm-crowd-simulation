"""补充系统内置人群模板种子数据。"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260413_0014"
down_revision = "20260413_0013"
branch_labels = None
depends_on = None

crowd_type_templates = sa.table(
    "crowd_type_templates",
    sa.column("type_id", sa.String(length=32)),
    sa.column("display_name", sa.String(length=32)),
    sa.column("color_hex", sa.String(length=7)),
    sa.column("is_builtin", sa.Boolean()),
    sa.column("base_speed", sa.Numeric(4, 2)),
    sa.column("social_distance", sa.Numeric(4, 2)),
    sa.column("tolerance_duration_ms", sa.Integer()),
    sa.column("attraction_weights", postgresql.JSONB(astext_type=sa.Text())),
    sa.column("metadata", postgresql.JSONB(astext_type=sa.Text())),
)

BUILTIN_ROWS = [
    {
        "type_id": "tourist",
        "display_name": "观光游客",
        "color_hex": "#FF6B35",
        "is_builtin": True,
        "base_speed": 1.20,
        "social_distance": 0.80,
        "tolerance_duration_ms": 20000,
        "attraction_weights": {
            "exhibit": 0.90,
            "commercial": 0.60,
            "rest": 0.30,
            "service": 0.20,
            "plaza": 0.50,
            "passage": 0.10,
            "exit_final": 0.05,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
    {
        "type_id": "shopper",
        "display_name": "购物者",
        "color_hex": "#E91E63",
        "is_builtin": True,
        "base_speed": 0.95,
        "social_distance": 0.70,
        "tolerance_duration_ms": 30000,
        "attraction_weights": {
            "exhibit": 0.30,
            "commercial": 0.95,
            "rest": 0.50,
            "service": 0.10,
            "plaza": 0.40,
            "passage": 0.10,
            "exit_final": 0.10,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
    {
        "type_id": "commuter",
        "display_name": "通勤者",
        "color_hex": "#2196F3",
        "is_builtin": True,
        "base_speed": 1.45,
        "social_distance": 0.60,
        "tolerance_duration_ms": 12000,
        "attraction_weights": {
            "exhibit": 0.10,
            "commercial": 0.15,
            "rest": 0.10,
            "service": 0.20,
            "plaza": 0.20,
            "passage": 0.95,
            "exit_final": 0.80,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
    {
        "type_id": "resident",
        "display_name": "居民",
        "color_hex": "#4CAF50",
        "is_builtin": True,
        "base_speed": 1.05,
        "social_distance": 0.90,
        "tolerance_duration_ms": 28000,
        "attraction_weights": {
            "exhibit": 0.25,
            "commercial": 0.20,
            "rest": 0.70,
            "service": 0.45,
            "plaza": 0.35,
            "passage": 0.25,
            "exit_final": 0.15,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
    {
        "type_id": "student",
        "display_name": "学生",
        "color_hex": "#FF9800",
        "is_builtin": True,
        "base_speed": 1.30,
        "social_distance": 0.65,
        "tolerance_duration_ms": 18000,
        "attraction_weights": {
            "exhibit": 0.55,
            "commercial": 0.35,
            "rest": 0.25,
            "service": 0.20,
            "plaza": 0.55,
            "passage": 0.45,
            "exit_final": 0.20,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
    {
        "type_id": "staff",
        "display_name": "工作人员",
        "color_hex": "#9E9E9E",
        "is_builtin": True,
        "base_speed": 1.10,
        "social_distance": 0.75,
        "tolerance_duration_ms": 45000,
        "attraction_weights": {
            "exhibit": 0.20,
            "commercial": 0.15,
            "rest": 0.30,
            "service": 0.95,
            "plaza": 0.25,
            "passage": 0.60,
            "exit_final": 0.10,
        },
        "metadata": {"seed_source": "system_builtin_v1", "builtin_version": 1},
    },
]


def upgrade() -> None:
    """执行升级迁移。"""

    insert_stmt = postgresql.insert(crowd_type_templates).values(BUILTIN_ROWS)
    op.execute(insert_stmt.on_conflict_do_nothing(index_elements=["type_id"]))


def downgrade() -> None:
    """执行回滚迁移。"""

    op.execute(
        sa.text(
            """
            DELETE FROM crowd_type_templates
            WHERE is_builtin = true
              AND type_id = ANY(:type_ids)
              AND metadata ->> 'seed_source' = 'system_builtin_v1'
            """
        ).bindparams(sa.bindparam("type_ids", [row["type_id"] for row in BUILTIN_ROWS], type_=postgresql.ARRAY(sa.Text())))
    )
