"""TimescaleDB 能力探测与状态查询。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection


def inspect_timescaledb(connection: Connection) -> dict[str, Any]:
    """探测当前数据库中的 TimescaleDB 可用性与轨迹表状态。"""

    available_row = connection.execute(
        text(
            """
            SELECT name, default_version, installed_version
            FROM pg_available_extensions
            WHERE name = 'timescaledb'
            """
        )
    ).mappings().first()

    available = available_row is not None
    installed = bool(
        connection.execute(
            text(
                """
                SELECT 1
                FROM pg_extension
                WHERE extname = 'timescaledb'
                """
            )
        ).scalar()
    )

    trajectory_points_hypertable = False
    compression_enabled = False
    chunk_interval_ms: int | None = None
    integer_now_func: str | None = None
    compression_settings: dict[str, Any] | None = None
    jobs: dict[str, Any] = {
        "compression_policy": None,
        "retention_policy": None,
    }
    if installed:
        hypertable_row = connection.execute(
            text(
                """
                SELECT compression_enabled
                FROM timescaledb_information.hypertables
                WHERE hypertable_schema = current_schema()
                  AND hypertable_name = 'trajectory_points'
                """
            )
        ).mappings().first()
        trajectory_points_hypertable = hypertable_row is not None
        compression_enabled = bool(hypertable_row["compression_enabled"]) if hypertable_row else False

        dimension_row = connection.execute(
            text(
                """
                SELECT integer_interval, integer_now_func
                FROM timescaledb_information.dimensions
                WHERE hypertable_schema = current_schema()
                  AND hypertable_name = 'trajectory_points'
                ORDER BY dimension_number
                LIMIT 1
                """
            )
        ).mappings().first()
        if dimension_row:
            chunk_interval_ms = (
                int(dimension_row["integer_interval"])
                if dimension_row["integer_interval"] is not None
                else None
            )
            integer_now_func = dimension_row["integer_now_func"]

        compression_row = connection.execute(
            text(
                """
                SELECT segmentby, orderby, compress_interval_length
                FROM timescaledb_information.hypertable_compression_settings
                WHERE hypertable = to_regclass(current_schema() || '.trajectory_points')
                ORDER BY index
                LIMIT 1
                """
            )
        ).mappings().first()
        if compression_row:
            compression_settings = {
                "segmentby": compression_row["segmentby"],
                "orderby": compression_row["orderby"],
                "compress_interval_length": compression_row["compress_interval_length"],
            }

        job_rows = connection.execute(
            text(
                """
                SELECT job_id, proc_name, scheduled, schedule_interval, config, next_start
                FROM timescaledb_information.jobs
                WHERE hypertable_schema = current_schema()
                  AND hypertable_name = 'trajectory_points'
                ORDER BY job_id
                """
            )
        ).mappings().all()
        for row in job_rows:
            job_info = {
                "job_id": int(row["job_id"]),
                "scheduled": bool(row["scheduled"]),
                "schedule_interval": str(row["schedule_interval"]) if row["schedule_interval"] is not None else None,
                "config": row["config"],
                "next_start": row["next_start"].isoformat() if row["next_start"] is not None else None,
            }
            if row["proc_name"] in {"policy_compression", "policy_columnstore"}:
                jobs["compression_policy"] = job_info
            elif row["proc_name"] == "policy_retention":
                jobs["retention_policy"] = job_info

    return {
        "available": available,
        "installed": installed,
        "ready": installed and trajectory_points_hypertable,
        "default_version": available_row["default_version"] if available_row else None,
        "installed_version": available_row["installed_version"] if available_row else None,
        "trajectory_points_hypertable": trajectory_points_hypertable,
        "trajectory_points_chunk_interval_ms": chunk_interval_ms,
        "trajectory_points_integer_now_func": integer_now_func,
        "trajectory_points_compression_enabled": compression_enabled,
        "trajectory_points_compression_settings": compression_settings,
        "trajectory_points_jobs": jobs,
    }
