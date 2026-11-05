"""轨迹写入与轨迹查询仓储。"""

from __future__ import annotations

from sqlalchemy import Select, and_, delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import (
    TrajectoryBatch,
    TrajectoryPoint,
    TrajectoryPointSampled1s,
)


class TrajectoryBatchRepository:
    """轨迹批次仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, batch: TrajectoryBatch) -> TrajectoryBatch:
        self.session.add(batch)
        return batch

    def add_if_absent(self, batch: TrajectoryBatch) -> bool:
        stmt = (
            postgresql_insert(TrajectoryBatch)
            .values(
                experiment_id=batch.experiment_id,
                batch_id=batch.batch_id,
                simulation_time_ms=batch.simulation_time_ms,
                sample_interval_ms=batch.sample_interval_ms,
                point_count=batch.point_count,
                accepted_count=batch.accepted_count,
                rejected_count=batch.rejected_count,
            )
            .on_conflict_do_nothing(
                constraint="uk_trajectory_batches_experiment_id_batch_id",
            )
            .returning(TrajectoryBatch.id)
        )
        inserted_batch_id = self.session.execute(stmt).scalar_one_or_none()
        return inserted_batch_id is not None

    def get_by_experiment_and_batch_id(self, experiment_pk: int, batch_id: str) -> TrajectoryBatch | None:
        stmt = select(TrajectoryBatch).where(
            TrajectoryBatch.experiment_id == experiment_pk,
            TrajectoryBatch.batch_id == batch_id,
        )
        return self.session.scalar(stmt)

    def update_counts(
        self,
        *,
        experiment_pk: int,
        batch_id: str,
        point_count: int,
        accepted_count: int,
        rejected_count: int,
    ) -> None:
        self.session.execute(
            update(TrajectoryBatch)
            .where(
                TrajectoryBatch.experiment_id == experiment_pk,
                TrajectoryBatch.batch_id == batch_id,
            )
            .values(
                point_count=point_count,
                accepted_count=accepted_count,
                rejected_count=rejected_count,
            )
        )


class TrajectoryPointRepository:
    """原始轨迹点仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, points: list[TrajectoryPoint]) -> list[TrajectoryPoint]:
        self.session.add_all(points)
        return points

    def add_all_ignore_conflicts(self, points: list[TrajectoryPoint]) -> list[str]:
        if not points:
            return []

        stmt = (
            postgresql_insert(TrajectoryPoint)
            .values(
                [
                    {
                        "experiment_id": point.experiment_id,
                        "agent_id": point.agent_id,
                        "simulation_time_ms": point.simulation_time_ms,
                        "crowd_type": point.crowd_type,
                        "zone_id": point.zone_id,
                        "position_x": point.position_x,
                        "position_z": point.position_z,
                        "speed": point.speed,
                        "state": point.state,
                        "target_exit_id": point.target_exit_id,
                        "is_queuing": point.is_queuing,
                        "sample_interval_ms": point.sample_interval_ms,
                        "batch_id": point.batch_id,
                        "ingested_at": point.ingested_at,
                    }
                    for point in points
                ]
            )
            .on_conflict_do_nothing(
                index_elements=[
                    TrajectoryPoint.experiment_id,
                    TrajectoryPoint.agent_id,
                    TrajectoryPoint.simulation_time_ms,
                ]
            )
            .returning(TrajectoryPoint.agent_id)
        )
        return [str(agent_id) for agent_id in self.session.execute(stmt).scalars().all()]

    def count_by_experiment_id(self, experiment_pk: int) -> int:
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(TrajectoryPoint)
            .where(TrajectoryPoint.experiment_id == experiment_pk)
        )
        return int(self.session.scalar(stmt) or 0)

    def list_existing_agent_ids(
        self,
        *,
        experiment_pk: int,
        simulation_time_ms: int,
        agent_ids: list[str],
    ) -> set[str]:
        if not agent_ids:
            return set()

        stmt = select(TrajectoryPoint.agent_id).where(
            TrajectoryPoint.experiment_id == experiment_pk,
            TrajectoryPoint.simulation_time_ms == simulation_time_ms,
            TrajectoryPoint.agent_id.in_(agent_ids),
        )
        return set(self.session.scalars(stmt))

    def list_last_points_by_agent(self, experiment_pk: int) -> list[TrajectoryPoint]:
        latest_points = (
            select(
                TrajectoryPoint.agent_id.label("agent_id"),
                func.max(TrajectoryPoint.simulation_time_ms).label("simulation_time_ms"),
            )
            .where(TrajectoryPoint.experiment_id == experiment_pk)
            .group_by(TrajectoryPoint.agent_id)
            .subquery()
        )
        stmt = (
            select(TrajectoryPoint)
            .join(
                latest_points,
                and_(
                    TrajectoryPoint.agent_id == latest_points.c.agent_id,
                    TrajectoryPoint.simulation_time_ms == latest_points.c.simulation_time_ms,
                ),
            )
            .where(TrajectoryPoint.experiment_id == experiment_pk)
            .order_by(TrajectoryPoint.agent_id)
        )
        return list(self.session.scalars(stmt))


class TrajectoryPointSampledRepository:
    """秒级采样轨迹点仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, points: list[TrajectoryPointSampled1s]) -> list[TrajectoryPointSampled1s]:
        self.session.add_all(points)
        return points

    def count_by_experiment_id(self, experiment_pk: int) -> int:
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(TrajectoryPointSampled1s)
            .where(TrajectoryPointSampled1s.experiment_id == experiment_pk)
        )
        return int(self.session.scalar(stmt) or 0)

    def delete_by_experiment(self, experiment_pk: int) -> None:
        self.session.execute(
            delete(TrajectoryPointSampled1s).where(TrajectoryPointSampled1s.experiment_id == experiment_pk)
        )
