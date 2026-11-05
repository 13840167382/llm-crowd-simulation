"""分析层聚合表与缓存表仓储。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import (
    AgentMetric1m,
    DecisionMetric1m,
    ExitMetric1m,
    HeatmapCache,
    ZoneMetric1m,
)


class ZoneMetric1mRepository:
    """区域分钟聚合仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, rows: list[ZoneMetric1m]) -> list[ZoneMetric1m]:
        self.session.add_all(rows)
        return rows


class ExitMetric1mRepository:
    """出口分钟聚合仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, rows: list[ExitMetric1m]) -> list[ExitMetric1m]:
        self.session.add_all(rows)
        return rows


class DecisionMetric1mRepository:
    """决策分钟聚合仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, rows: list[DecisionMetric1m]) -> list[DecisionMetric1m]:
        self.session.add_all(rows)
        return rows


class AgentMetric1mRepository:
    """人物概览分钟聚合仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, rows: list[AgentMetric1m]) -> list[AgentMetric1m]:
        self.session.add_all(rows)
        return rows


class HeatmapCacheRepository:
    """热力图缓存仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, row: HeatmapCache) -> HeatmapCache:
        self.session.add(row)
        return row

    def save(self, row: HeatmapCache) -> HeatmapCache:
        existing = self.get_by_cache_key(row.cache_key)
        if existing is None:
            self.session.add(row)
            return row

        existing.experiment_id = row.experiment_id
        existing.metric = row.metric
        existing.time_start_ms = row.time_start_ms
        existing.time_end_ms = row.time_end_ms
        existing.crowd_type_filter = row.crowd_type_filter
        existing.zone_id_filter = row.zone_id_filter
        existing.normalization = row.normalization
        existing.grid_hash = row.grid_hash
        existing.statistics_json = row.statistics_json
        existing.cells = row.cells
        existing.expires_at = row.expires_at
        existing.created_at = row.created_at
        return existing

    def get_by_cache_key(self, cache_key: str) -> HeatmapCache | None:
        stmt = select(HeatmapCache).where(HeatmapCache.cache_key == cache_key)
        return self.session.scalar(stmt)

    def delete_by_cache_key(self, cache_key: str) -> None:
        self.session.execute(delete(HeatmapCache).where(HeatmapCache.cache_key == cache_key))
