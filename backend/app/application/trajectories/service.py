"""轨迹写入与派生视图刷新服务。

本服务只同步保证“原始轨迹批次可靠入库”和“批次幂等”。
分钟聚合、人物概览和秒级采样轨迹属于派生视图，写入成功后通过 RQ 异步刷新；
如果 RQ 投递失败，原始轨迹仍保留，后续可以通过维护任务或手动命令补刷新。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from backend.app.application.analytics.aggregation import (
    bucket_start_ms,
    refresh_agent_metrics_for_times,
    refresh_trajectory_metrics_for_times,
    refresh_trajectory_samples_for_agents,
)
from backend.app.application.common import raise_experiment_status_conflict
from backend.app.application.experiments.service import get_experiment_or_404
from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import ExperimentStatus
from backend.app.infrastructure.db.models import TrajectoryBatch, TrajectoryPoint
from backend.app.infrastructure.db.repositories import TrajectoryBatchRepository, TrajectoryPointRepository
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.trajectories import (
    TrajectoryBatchRequest,
    TrajectoryBatchResponseData,
    TrajectoryPointIngestItem,
)

logger = get_logger(__name__)


@dataclass(slots=True)
class TrajectoryRefreshResult:
    """轨迹派生视图刷新结果。

    RQ worker 会把这个结果写入日志或任务结果中，用于确认刷新了哪些时间桶和人物。
    """

    experiment_id: str
    status: str
    simulation_time_count: int
    refreshed_trajectory_buckets: int
    refreshed_agent_buckets: int
    sampled_agent_count: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def create_trajectory_batch(
    session: Session,
    *,
    experiment_id: str,
    payload: TrajectoryBatchRequest,
) -> TrajectoryBatchResponseData:
    """写入原始轨迹批次，并把派生刷新推给 RQ。

    轨迹批次以 `(experiment_id, batch_id)` 做幂等。批次第一次到达时写入
    `trajectory_batches` 和 `trajectory_points`；重复批次直接返回已有计数。
    单个轨迹点以 `(experiment_id, agent_id, simulation_time_ms)` 去重。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    if experiment.status != ExperimentStatus.RUNNING:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态 `{experiment.status}` 不允许继续写入轨迹批次",
            data={"status": experiment.status},
        )

    batch_repository = TrajectoryBatchRepository(session)
    point_repository = TrajectoryPointRepository(session)

    batch = TrajectoryBatch(
        experiment_id=experiment.id,
        batch_id=payload.batch_id,
        simulation_time_ms=payload.simulation_time_ms,
        sample_interval_ms=payload.sample_interval_ms,
        point_count=len(payload.points),
        accepted_count=0,
        rejected_count=len(payload.points),
    )
    inserted_batch = batch_repository.add_if_absent(batch)
    if not inserted_batch:
        # 批次已经存在时不再重复写点，直接回放已有统计，保证 Unity 重试安全。
        existing_batch = batch_repository.get_by_experiment_and_batch_id(experiment.id, payload.batch_id)
        if existing_batch is None:
            raise RuntimeError(
                f"轨迹批次 `{payload.batch_id}` 已存在但无法读取回放结果: experiment_id={experiment.experiment_id}"
            )
        logger.info(
            "轨迹批次命中 batch_id 幂等回放: experiment_id=%s batch_id=%s",
            experiment.experiment_id,
            payload.batch_id,
        )
        return TrajectoryBatchResponseData(
            batch_id=existing_batch.batch_id,
            accepted_count=existing_batch.accepted_count,
            rejected_count=existing_batch.rejected_count,
            total_stored_points=point_repository.count_by_experiment_id(experiment.id),
        )

    ingested_at = datetime.now(timezone.utc)
    point_rows = [
        _build_trajectory_point(
            experiment_pk=experiment.id,
            batch_id=payload.batch_id,
            payload=payload,
            point=point,
            ingested_at=ingested_at,
        )
        for point in payload.points
    ]
    accepted_agent_ids = point_repository.add_all_ignore_conflicts(point_rows)
    accepted_count = len(accepted_agent_ids)
    rejected_count = len(payload.points) - accepted_count
    batch_repository.update_counts(
        experiment_pk=experiment.id,
        batch_id=payload.batch_id,
        point_count=len(payload.points),
        accepted_count=accepted_count,
        rejected_count=rejected_count,
    )
    session.commit()

    _enqueue_trajectory_refresh(
        experiment_id=experiment.experiment_id,
        batch_id=payload.batch_id,
        simulation_time_ms_values=[payload.simulation_time_ms],
        agent_ids=accepted_agent_ids,
    )

    logger.info(
        "轨迹批次已写入原始轨迹: experiment_id=%s batch_id=%s accepted=%s rejected=%s",
        experiment.experiment_id,
        payload.batch_id,
        accepted_count,
        rejected_count,
    )
    return TrajectoryBatchResponseData(
        batch_id=payload.batch_id,
        accepted_count=accepted_count,
        rejected_count=rejected_count,
        total_stored_points=point_repository.count_by_experiment_id(experiment.id),
    )


def refresh_trajectory_views(
    session: Session,
    *,
    experiment_id: str,
    simulation_time_ms_values: list[int],
    agent_ids: list[str],
) -> TrajectoryRefreshResult:
    """刷新指定时间和人物范围内的轨迹派生视图。

    该函数通常由 RQ 轨迹刷新任务调用。它只处理本次批次涉及的模拟时间和人物，
    避免每次轨迹上报都重建全实验聚合。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    unique_time_values = sorted({int(value) for value in simulation_time_ms_values if int(value) >= 0})
    unique_agent_ids = sorted({str(agent_id) for agent_id in agent_ids if str(agent_id)})

    if unique_time_values:
        refresh_trajectory_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=unique_time_values,
        )
        refresh_agent_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=unique_time_values,
        )
    if unique_time_values and unique_agent_ids:
        refresh_trajectory_samples_for_agents(
            session,
            experiment_pk=experiment.id,
            agent_ids=unique_agent_ids,
            simulation_time_ms_values=unique_time_values,
        )
    session.commit()

    return TrajectoryRefreshResult(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        simulation_time_count=len(unique_time_values),
        refreshed_trajectory_buckets=len({bucket_start_ms(value) for value in unique_time_values}),
        refreshed_agent_buckets=len({bucket_start_ms(value) for value in unique_time_values}),
        sampled_agent_count=len(unique_agent_ids),
    )


def _build_trajectory_point(
    *,
    experiment_pk: int,
    batch_id: str,
    payload: TrajectoryBatchRequest,
    point: TrajectoryPointIngestItem,
    ingested_at: datetime,
) -> TrajectoryPoint:
    """把接口 DTO 转成原始轨迹 ORM 行。

    坐标和速度在入库前做精度收口，减少浮点噪声对唯一性、聚合和热力图分桶的影响。
    """

    return TrajectoryPoint(
        experiment_id=experiment_pk,
        agent_id=point.agent_id,
        simulation_time_ms=payload.simulation_time_ms,
        crowd_type=point.crowd_type,
        zone_id=point.zone_id,
        position_x=round_coordinate(point.position.x),
        position_z=round_coordinate(point.position.z),
        speed=round(point.speed, 2),
        state=point.state,
        target_exit_id=point.target_exit_id,
        is_queuing=point.is_queuing,
        sample_interval_ms=payload.sample_interval_ms,
        batch_id=batch_id,
        ingested_at=ingested_at,
    )


def _enqueue_trajectory_refresh(
    *,
    experiment_id: str,
    batch_id: str,
    simulation_time_ms_values: list[int],
    agent_ids: list[str],
) -> None:
    """投递轨迹派生刷新任务。

    这里故意吞掉 RQ 投递异常：接口主路径已经完成原始数据写入，异步刷新失败
    不应该导致 Unity 认为轨迹上报失败。后续可通过维护命令补刷新。
    """

    if not simulation_time_ms_values or not agent_ids:
        return

    try:
        from backend.app.infrastructure.workers.rq_worker import enqueue_trajectory_refresh_job

        dispatch_result = enqueue_trajectory_refresh_job(
            experiment_id=experiment_id,
            batch_id=batch_id,
            simulation_time_ms_values=simulation_time_ms_values,
            agent_ids=agent_ids,
        )
        logger.info(
            "轨迹刷新异步任务已投递: experiment_id=%s batch_id=%s status=%s rq_job_id=%s",
            experiment_id,
            batch_id,
            dispatch_result.status,
            dispatch_result.rq_job_id,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "轨迹刷新异步任务投递失败，已保留原始轨迹数据: experiment_id=%s batch_id=%s error=%s",
            experiment_id,
            batch_id,
            exc,
        )
