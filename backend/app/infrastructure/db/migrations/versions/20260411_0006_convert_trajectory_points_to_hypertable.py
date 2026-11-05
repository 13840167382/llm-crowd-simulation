"""将轨迹点表迁移为 TimescaleDB hypertable。"""

from __future__ import annotations

from alembic import op

revision = "20260411_0006"
down_revision = "20260411_0005"
branch_labels = None
depends_on = None

TRAJECTORY_POINTS_CHUNK_INTERVAL_MS = 3_600_000


def upgrade() -> None:
    """将 trajectory_points 转换为按毫秒时间分区的 hypertable。"""

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM pg_extension
                WHERE extname = 'timescaledb'
            ) THEN
                RAISE EXCEPTION '当前数据库尚未启用 TimescaleDB 扩展，请先执行 CREATE EXTENSION IF NOT EXISTS timescaledb';
            END IF;
        END
        $$;
        """
    )

    # simulation_time_ms 使用毫秒整数，按 1 小时切块更适合当前实验链路的查询窗口。
    op.execute(
        f"""
        SELECT create_hypertable(
            'trajectory_points',
            by_range('simulation_time_ms', {TRAJECTORY_POINTS_CHUNK_INTERVAL_MS}),
            migrate_data => TRUE,
            if_not_exists => TRUE,
            create_default_indexes => FALSE
        );
        """
    )

    # 如果表已经被手动转换过，也统一把 chunk interval 校正到项目约定值。
    op.execute(
        f"""
        SELECT set_chunk_time_interval(
            'trajectory_points',
            {TRAJECTORY_POINTS_CHUNK_INTERVAL_MS}
        );
        """
    )


def downgrade() -> None:
    """Timescale hypertable 回滚需要人工确认。"""

    raise RuntimeError(
        "暂不支持自动将 trajectory_points 从 TimescaleDB hypertable 回滚为普通表，请先手动处理后再执行降级。"
    )
