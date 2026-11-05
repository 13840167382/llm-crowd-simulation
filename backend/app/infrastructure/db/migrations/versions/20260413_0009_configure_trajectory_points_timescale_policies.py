"""为 trajectory_points 配置 TimescaleDB 压缩参数。"""

from __future__ import annotations

from alembic import op

revision = "20260413_0009"
down_revision = "20260413_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """启用轨迹时序表的压缩配置。"""

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

    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1
                FROM timescaledb_information.hypertables
                WHERE hypertable_schema = current_schema()
                  AND hypertable_name = 'trajectory_points'
            ) THEN
                RAISE EXCEPTION 'trajectory_points 尚未迁移为 TimescaleDB hypertable，请先执行前置迁移 20260411_0006';
            END IF;
        END
        $$;
        """
    )

    # 当前查询几乎都会先按 experiment_id 收敛，再按 simulation_time_ms 顺序回放或聚合；
    # 因此压缩配置优先保留实验内时序扫描的局部性。
    op.execute(
        """
        ALTER TABLE trajectory_points
        SET (
            timescaledb.compress = true,
            timescaledb.compress_segmentby = 'experiment_id',
            timescaledb.compress_orderby = 'simulation_time_ms ASC'
        );
        """
    )

    # 这里不直接创建 add_retention_policy / add_compression_policy：
    # trajectory_points 当前以 simulation_time_ms 作为相对时间维度，
    # 每个实验都会从 0 重新开始，直接按 chunk 时间自动删除有误删风险。


def downgrade() -> None:
    """移除轨迹时序表的压缩配置。"""

    op.execute(
        """
        ALTER TABLE trajectory_points
        RESET (
            timescaledb.compress_segmentby,
            timescaledb.compress_orderby
        );
        """
    )
    op.execute(
        """
        ALTER TABLE trajectory_points
        SET (
            timescaledb.compress = false
        );
        """
    )
