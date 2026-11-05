"""实验级维护、归档与清理服务。

实验完成后不应长期保留高频原始轨迹和决策明细，否则数据库会快速膨胀。
本模块负责把“完成实验后的收尾动作”拆成可重试的维护任务：刷新分钟聚合、预热热力图、
清理原始运行数据、写入归档快照。每个任务都要求幂等，便于 RQ worker 失败后重试，
也便于运维通过命令行重置或重放某段维护链路。
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from enum import Enum
from time import time
from uuid import uuid4

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from backend.app.api import error_codes
from backend.app.api.errors import AppError
from backend.app.application.common import (
    datetime_to_timestamp_ms,
    raise_experiment_not_found,
    raise_experiment_status_conflict,
    raise_invalid_field_value,
)
from backend.app.core.config import get_settings
from backend.app.core.context import reset_request_context, set_request_context
from backend.app.domain.enums import ExperimentStatus, MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    DecisionBatch,
    DecisionMetric1m,
    Experiment,
    ExperimentAgent,
    ExperimentAllocation,
    ExperimentArchive,
    ExperimentExit,
    ExperimentMaintenanceJob,
    ExperimentZone,
    ExitMetric1m,
    HeatmapCache,
    TrajectoryBatch,
    TrajectoryPoint,
    TrajectoryPointSampled1s,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.repositories import ExperimentArchiveRepository, ExperimentMaintenanceJobRepository
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


@contextmanager
def _maintenance_job_logging_context(
    *,
    experiment_id: str,
    maintenance_job_id: int,
    job_type: MaintenanceJobType,
):
    """为后台维护任务注入实验上下文，便于日志排查。"""

    request_id = f"maintenance-job-{maintenance_job_id}-{uuid4().hex[:8]}"
    tokens = set_request_context(request_id=request_id, experiment_id=experiment_id)
    logger.info(
        "维护任务进入执行上下文: experiment_id=%s maintenance_job_id=%s job_type=%s request_id=%s",
        experiment_id,
        maintenance_job_id,
        job_type.value,
        request_id,
    )
    try:
        yield request_id
    finally:
        logger.info(
            "维护任务退出执行上下文: experiment_id=%s maintenance_job_id=%s job_type=%s request_id=%s",
            experiment_id,
            maintenance_job_id,
            job_type.value,
            request_id,
        )
        reset_request_context(tokens)

RAW_RUNTIME_TABLES = {
    "decision_batches": DecisionBatch,
    "agent_decisions": AgentDecision,
    "trajectory_batches": TrajectoryBatch,
    "trajectory_points": TrajectoryPoint,
}

EXPERIMENT_ARCHIVE_VERSION = "v1"


@dataclass(slots=True)
class ExperimentRuntimeCleanupResult:
    """实验级原始运行数据清理结果。"""

    experiment_id: str
    status: str
    already_cleaned: bool
    cleaned_at_ms: int
    deleted_counts: dict[str, int]
    preserved_tables: list[str]
    warnings: list[str]

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class MaintenanceJobExecutionResult:
    """维护任务执行汇总。"""

    processed_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    retried_count: int
    job_ids: list[int]

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class ExperimentAnalyticsRefreshResult:
    """实验聚合刷新结果。"""

    experiment_id: str
    status: str
    decision_time_count: int
    trajectory_time_count: int
    refreshed_decision_buckets: int
    refreshed_trajectory_buckets: int

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class ExperimentHeatmapPrewarmResult:
    """实验热力图预热结果。"""

    experiment_id: str
    prewarmed_count: int
    cache_hit_count: int
    time_start_ms: int | None
    time_end_ms: int | None
    grid_config: dict[str, object] | None
    crowd_types: list[str]
    requests: list[dict[str, object]]

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class ExperimentArchiveResult:
    """实验归档结果。"""

    experiment_id: str
    status: str
    already_archived: bool
    archived_at_ms: int
    cleanup_snapshot_present: bool
    raw_runtime_retained: bool
    raw_runtime_remaining_counts: dict[str, int]
    aggregate_snapshot: dict[str, int]
    heatmap_cache_count: int
    maintenance_job_statuses: dict[str, str]
    warnings: list[str]

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class MaintenanceJobRecord:
    """单条维护任务状态快照。"""

    job_id: int
    experiment_id: str
    job_type: str
    status: str
    attempt_count: int
    created_at_ms: int
    updated_at_ms: int
    scheduled_at_ms: int | None
    started_at_ms: int | None
    finished_at_ms: int | None
    error_message: str | None
    payload_json: dict[str, object] | None
    result_json: dict[str, object] | None

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


@dataclass(slots=True)
class MaintenanceJobQueryResult:
    """维护任务状态查询结果。"""

    experiment_id: str
    total_count: int
    status_counts: dict[str, int]
    job_type_counts: dict[str, int]
    jobs: list[MaintenanceJobRecord]

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return {
            "experiment_id": self.experiment_id,
            "total_count": self.total_count,
            "status_counts": self.status_counts,
            "job_type_counts": self.job_type_counts,
            "jobs": [job.as_dict() for job in self.jobs],
        }


@dataclass(slots=True)
class MaintenanceJobReplayResult:
    """维护任务重置或重放结果。"""

    experiment_id: str
    action: str
    reset_count: int
    affected_job_ids: list[int]
    affected_job_types: list[str]
    cleared_summary_keys: list[str]
    dispatch_result: dict[str, object] | None
    message: str

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志或命令行输出的字典。"""

        return asdict(self)


def query_maintenance_jobs(
    session: Session,
    *,
    experiment_id: str,
    status: MaintenanceJobStatus | None = None,
    limit: int = 50,
    include_payload: bool = False,
    include_result: bool = False,
) -> MaintenanceJobQueryResult:
    """查询某个实验的维护任务状态。

    返回结果同时包含当前筛选出的任务列表，以及该实验所有维护任务按状态和类型统计的
    汇总计数，便于命令行或后台页面快速判断链路卡在哪一步。
    """

    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    repository = ExperimentMaintenanceJobRepository(session)
    jobs = repository.list_by_experiment(
        experiment_pk=experiment.id,
        statuses={status} if status is not None else None,
        limit=max(limit, 1),
    )

    all_jobs = repository.list_by_experiment(experiment_pk=experiment.id)
    status_counts: dict[str, int] = {}
    job_type_counts: dict[str, int] = {}
    for job in all_jobs:
        status_counts[job.status.value] = status_counts.get(job.status.value, 0) + 1
        job_type_counts[job.job_type.value] = job_type_counts.get(job.job_type.value, 0) + 1

    return MaintenanceJobQueryResult(
        experiment_id=experiment.experiment_id,
        total_count=len(all_jobs),
        status_counts=status_counts,
        job_type_counts=job_type_counts,
        jobs=[
            _build_job_record(
                experiment_id=experiment.experiment_id,
                job=job,
                include_payload=include_payload,
                include_result=include_result,
            )
            for job in jobs
        ],
    )


def reset_failed_maintenance_jobs(
    session: Session,
    *,
    experiment_id: str,
    job_id: int | None = None,
    dispatch: bool = True,
) -> MaintenanceJobReplayResult:
    """把失败维护任务重置为待执行，并自动补派发到 RQ。

    可以只重置一个失败 job，也可以重置该实验下所有失败 job。重置时会清掉对应
    summary_json 标记，避免“任务重跑但旧摘要让实现误判已经完成”。
    """

    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    repository = ExperimentMaintenanceJobRepository(session)

    if job_id is not None:
        target_job = repository.get_by_id(job_id)
        if target_job is None or target_job.experiment_id != experiment.id:
            raise_invalid_field_value(f"维护任务 `{job_id}` 不存在，或不属于实验 `{experiment_id}`")
        if target_job.status == MaintenanceJobStatus.RUNNING:
            raise _build_maintenance_job_conflict(
                f"维护任务 `{job_id}` 正在执行中，暂时不能重置",
                data={"job_id": job_id, "status": target_job.status.value},
            )
        if target_job.status != MaintenanceJobStatus.FAILED:
            raise_invalid_field_value(
                f"维护任务 `{job_id}` 当前状态为 `{target_job.status.value}`，仅支持重置失败任务",
                data={"job_id": job_id, "status": target_job.status.value},
            )
        jobs = [target_job]
    else:
        jobs = repository.list_by_experiment(
            experiment_pk=experiment.id,
            statuses={MaintenanceJobStatus.FAILED},
        )
        if not jobs:
            return MaintenanceJobReplayResult(
                experiment_id=experiment.experiment_id,
                action="reset_failed",
                reset_count=0,
                affected_job_ids=[],
                affected_job_types=[],
                cleared_summary_keys=[],
                dispatch_result=None,
                message="当前没有失败维护任务需要重置",
            )

    scheduled_at = datetime.now(timezone.utc)
    selected_job_types = {job.job_type for job in jobs}
    cleared_summary_keys = _clear_maintenance_summary_keys(experiment, selected_job_types=selected_job_types)
    for job in jobs:
        repository.reset_for_replay(job, scheduled_at=scheduled_at, reset_attempt_count=True)
    session.commit()

    dispatch_result = _dispatch_maintenance_jobs_after_mutation(dispatch=dispatch)
    logger.info(
        "失败维护任务重置完成: experiment_id=%s job_ids=%s dispatch_status=%s",
        experiment.experiment_id,
        [int(job.id) for job in jobs],
        dispatch_result.get("status") if dispatch_result else None,
    )
    return MaintenanceJobReplayResult(
        experiment_id=experiment.experiment_id,
        action="reset_failed",
        reset_count=len(jobs),
        affected_job_ids=[int(job.id) for job in jobs],
        affected_job_types=[job.job_type.value for job in jobs],
        cleared_summary_keys=cleared_summary_keys,
        dispatch_result=dispatch_result,
        message="失败维护任务已重置为待执行状态",
    )


def replay_maintenance_jobs_from(
    session: Session,
    *,
    experiment_id: str,
    from_job_id: int,
    dispatch: bool = True,
) -> MaintenanceJobReplayResult:
    """从某个维护任务开始重放该实验的后续维护链路。

    该能力用于运维修复部分数据后重新跑后续步骤，例如从热力图预热开始重跑清理和归档。
    正在 RUNNING 的维护任务不能被重放，避免一个任务被两个 worker 同时写结果。
    """

    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    repository = ExperimentMaintenanceJobRepository(session)
    target_job = repository.get_by_id(from_job_id)
    if target_job is None or target_job.experiment_id != experiment.id:
        raise_invalid_field_value(f"维护任务 `{from_job_id}` 不存在，或不属于实验 `{experiment_id}`")

    jobs = repository.list_from_job(
        experiment_pk=experiment.id,
        from_created_at=target_job.created_at,
        from_job_id=int(target_job.id),
    )
    if not jobs:
        raise_invalid_field_value(f"维护任务 `{from_job_id}` 之后没有可重放的任务")
    running_jobs = [job for job in jobs if job.status == MaintenanceJobStatus.RUNNING]
    if running_jobs:
        raise _build_maintenance_job_conflict(
            f"实验 `{experiment_id}` 仍有维护任务在执行中，暂时不能重放",
            data={"running_job_ids": [int(job.id) for job in running_jobs]},
        )

    scheduled_at = datetime.now(timezone.utc)
    selected_job_types = {job.job_type for job in jobs}
    cleared_summary_keys = _clear_maintenance_summary_keys(experiment, selected_job_types=selected_job_types)
    for job in jobs:
        repository.reset_for_replay(job, scheduled_at=scheduled_at, reset_attempt_count=True)
    session.commit()

    dispatch_result = _dispatch_maintenance_jobs_after_mutation(dispatch=dispatch)
    logger.info(
        "维护任务链路重放已重置: experiment_id=%s from_job_id=%s affected_job_ids=%s dispatch_status=%s",
        experiment.experiment_id,
        from_job_id,
        [int(job.id) for job in jobs],
        dispatch_result.get("status") if dispatch_result else None,
    )
    return MaintenanceJobReplayResult(
        experiment_id=experiment.experiment_id,
        action="replay_from_job",
        reset_count=len(jobs),
        affected_job_ids=[int(job.id) for job in jobs],
        affected_job_types=[job.job_type.value for job in jobs],
        cleared_summary_keys=cleared_summary_keys,
        dispatch_result=dispatch_result,
        message="维护任务链路已重置并准备重放",
    )


def enqueue_runtime_cleanup_job(
    session: Session,
    *,
    experiment: Experiment,
    purge_heatmap_cache: bool = True,
) -> ExperimentMaintenanceJob | None:
    """为已完成实验入队原始运行数据清理任务。"""

    settings = get_settings()
    if not settings.experiment_runtime_cleanup_auto_enqueue:
        logger.info(
            "实验原始数据清理自动入队已关闭: experiment_id=%s",
            experiment.experiment_id,
        )
        return None

    job = _enqueue_maintenance_job(
        session,
        experiment=experiment,
        job_type=MaintenanceJobType.RUNTIME_DATA_CLEANUP,
        payload_json={"purge_heatmap_cache": purge_heatmap_cache},
    )
    if job is None:
        return None
    logger.info(
        "实验原始数据清理任务已入队: experiment_id=%s purge_heatmap_cache=%s",
        experiment.experiment_id,
        purge_heatmap_cache,
    )
    return job


def enqueue_analytics_refresh_job(
    session: Session,
    *,
    experiment: Experiment,
) -> ExperimentMaintenanceJob | None:
    """为已完成实验入队聚合刷新任务。"""

    settings = get_settings()
    if not settings.experiment_runtime_cleanup_auto_enqueue:
        logger.info(
            "实验维护任务自动入队已关闭，跳过聚合刷新任务: experiment_id=%s",
            experiment.experiment_id,
        )
        return None

    job = _enqueue_maintenance_job(
        session,
        experiment=experiment,
        job_type=MaintenanceJobType.ANALYTICS_REFRESH,
        payload_json={},
    )
    if job is None:
        return None
    logger.info("实验聚合刷新任务已入队: experiment_id=%s", experiment.experiment_id)
    return job


def enqueue_heatmap_prewarm_job(
    session: Session,
    *,
    experiment: Experiment,
) -> ExperimentMaintenanceJob | None:
    """为已完成实验入队热力图缓存预热任务。"""

    settings = get_settings()
    if not settings.experiment_runtime_cleanup_auto_enqueue or not settings.heatmap_prewarm_enabled:
        logger.info(
            "实验热力图预热自动入队已关闭: experiment_id=%s",
            experiment.experiment_id,
        )
        return None

    job = _enqueue_maintenance_job(
        session,
        experiment=experiment,
        job_type=MaintenanceJobType.HEATMAP_PREWARM,
        payload_json={},
    )
    if job is None:
        return None
    logger.info("实验热力图预热任务已入队: experiment_id=%s", experiment.experiment_id)
    return job


def enqueue_experiment_archive_job(
    session: Session,
    *,
    experiment: Experiment,
) -> ExperimentMaintenanceJob | None:
    """为已完成实验入队归档任务。"""

    settings = get_settings()
    if not settings.experiment_runtime_cleanup_auto_enqueue:
        logger.info(
            "实验归档自动入队已关闭: experiment_id=%s",
            experiment.experiment_id,
        )
        return None

    job = _enqueue_maintenance_job(
        session,
        experiment=experiment,
        job_type=MaintenanceJobType.EXPERIMENT_ARCHIVE,
        payload_json={},
    )
    if job is None:
        return None
    logger.info("实验归档任务已入队: experiment_id=%s", experiment.experiment_id)
    return job


def enqueue_post_complete_jobs(
    session: Session,
    *,
    experiment: Experiment,
    purge_heatmap_cache: bool = True,
) -> list[ExperimentMaintenanceJob]:
    """在实验完成时按顺序入队维护任务。

    默认链路是 analytics_refresh -> heatmap_prewarm -> runtime_data_cleanup。
    archive 不在这里直接入队，而是在 runtime_data_cleanup 成功后入队，确保归档能看到
    清理摘要和最终维护任务状态。
    """

    jobs: list[ExperimentMaintenanceJob] = []

    analytics_job = enqueue_analytics_refresh_job(session, experiment=experiment)
    if analytics_job is not None:
        jobs.append(analytics_job)

    prewarm_job = enqueue_heatmap_prewarm_job(session, experiment=experiment)
    if prewarm_job is not None and all(existing.id != prewarm_job.id for existing in jobs):
        jobs.append(prewarm_job)

    cleanup_job = enqueue_runtime_cleanup_job(
        session,
        experiment=experiment,
        purge_heatmap_cache=purge_heatmap_cache if prewarm_job is None else False,
    )
    if cleanup_job is not None and all(existing.id != cleanup_job.id for existing in jobs):
        jobs.append(cleanup_job)

    return jobs


def _enqueue_maintenance_job(
    session: Session,
    *,
    experiment: Experiment,
    job_type: MaintenanceJobType,
    payload_json: dict[str, object],
) -> ExperimentMaintenanceJob | None:
    """按任务类型做幂等入队。

    同一实验同一任务类型如果已经 pending/running/succeeded，就复用最新记录。
    只有失败任务才允许重新创建或由 reset/replay 显式重置，避免重复清理和重复归档。
    """

    repository = ExperimentMaintenanceJobRepository(session)
    latest_job = repository.get_latest_by_experiment_and_type(
        experiment_pk=experiment.id,
        job_type=job_type,
    )
    if latest_job is not None and latest_job.status in {
        MaintenanceJobStatus.PENDING,
        MaintenanceJobStatus.RUNNING,
        MaintenanceJobStatus.SUCCEEDED,
    }:
        logger.info(
            "实验维护任务已存在，跳过重复入队: experiment_id=%s job_type=%s job_id=%s status=%s",
            experiment.experiment_id,
            job_type.value,
            latest_job.id,
            latest_job.status,
        )
        return latest_job

    job = ExperimentMaintenanceJob(
        experiment_id=experiment.id,
        job_type=job_type,
        status=MaintenanceJobStatus.PENDING,
        payload_json=payload_json,
        result_json={},
        scheduled_at=datetime.now(timezone.utc),
    )
    repository.add(job)
    return job


def run_pending_maintenance_jobs(
    session: Session,
    *,
    limit: int = 20,
) -> MaintenanceJobExecutionResult:
    """执行待处理的维护任务。

    这是 RQ worker 调用的核心循环。每次取一批 pending job，先把任务标记为 RUNNING 并提交，
    再执行具体维护动作；成功后立即写回结果，失败则按配置退避重试，超过最大次数才标记 FAILED。
    每个 job 独立提交，避免一个失败任务回滚掉前面已经成功的维护结果。
    """

    repository = ExperimentMaintenanceJobRepository(session)
    succeeded_count = 0
    failed_count = 0
    skipped_count = 0
    retried_count = 0
    job_ids: list[int] = []
    settings = get_settings()

    while len(job_ids) < limit:
        remaining = max(limit - len(job_ids), 0)
        if remaining <= 0:
            break

        jobs = repository.list_pending_jobs(limit=remaining)
        if not jobs:
            break

        for job in jobs:
            if len(job_ids) >= limit:
                break

            job_pk = int(job.id)
            job_ids.append(job_pk)
            started_at = datetime.now(timezone.utc)
            # 先提交 RUNNING 状态，防止多个 worker 同时捞到同一条 pending 任务。
            repository.mark_running(job, started_at=started_at)
            session.commit()

            experiment_id = _get_experiment_external_id(session, experiment_pk=job.experiment_id)
            # 给后台任务注入 request_id/experiment_id，统一进入结构化日志链路。
            request_id = f"maintenance-job-{job_pk}-{uuid4().hex[:8]}"
            tokens = set_request_context(request_id=request_id, experiment_id=experiment_id)
            logger.info(
                "维护任务进入执行上下文: experiment_id=%s maintenance_job_id=%s job_type=%s request_id=%s",
                experiment_id,
                job_pk,
                job.job_type.value,
                request_id,
            )

            try:
                finished_at = datetime.now(timezone.utc)

                if job.job_type == MaintenanceJobType.ANALYTICS_REFRESH:
                    result = refresh_experiment_analytics(session, experiment_id=experiment_id)
                    succeeded_job = session.get(ExperimentMaintenanceJob, job_pk)
                    if succeeded_job is None:
                        raise RuntimeError(f"维护任务 `{job_pk}` 不存在，无法写回成功状态")
                    repository.mark_succeeded(succeeded_job, result_json=result.as_dict(), finished_at=finished_at)
                    session.commit()
                    succeeded_count += 1
                    continue

                if job.job_type == MaintenanceJobType.HEATMAP_PREWARM:
                    result = prewarm_experiment_heatmaps(session, experiment_id=experiment_id)
                    succeeded_job = session.get(ExperimentMaintenanceJob, job_pk)
                    if succeeded_job is None:
                        raise RuntimeError(f"维护任务 `{job_pk}` 不存在，无法写回成功状态")
                    repository.mark_succeeded(succeeded_job, result_json=result.as_dict(), finished_at=finished_at)
                    session.commit()
                    succeeded_count += 1
                    continue

                if job.job_type == MaintenanceJobType.RUNTIME_DATA_CLEANUP:
                    result = cleanup_experiment_runtime_data(
                        session,
                        experiment_id=experiment_id,
                        purge_heatmap_cache=bool(job.payload_json.get("purge_heatmap_cache", True)),
                    )
                    # 清理成功后再入队归档任务，让归档摘要能记录清理结果和剩余数据量。
                    experiment = session.get(Experiment, job.experiment_id)
                    if experiment is not None:
                        enqueue_experiment_archive_job(session, experiment=experiment)
                    succeeded_job = session.get(ExperimentMaintenanceJob, job_pk)
                    if succeeded_job is None:
                        raise RuntimeError(f"维护任务 `{job_pk}` 不存在，无法写回成功状态")
                    repository.mark_succeeded(succeeded_job, result_json=result.as_dict(), finished_at=finished_at)
                    session.commit()
                    if result.already_cleaned:
                        skipped_count += 1
                    else:
                        succeeded_count += 1
                    continue

                if job.job_type == MaintenanceJobType.EXPERIMENT_ARCHIVE:
                    result = archive_experiment(session, experiment_id=experiment_id)
                    succeeded_job = session.get(ExperimentMaintenanceJob, job_pk)
                    if succeeded_job is None:
                        raise RuntimeError(f"维护任务 `{job_pk}` 不存在，无法写回成功状态")
                    repository.mark_succeeded(succeeded_job, result_json=result.as_dict(), finished_at=finished_at)
                    session.commit()
                    if result.already_archived:
                        skipped_count += 1
                    else:
                        succeeded_count += 1
                    continue

                repository.mark_failed(
                    job,
                    error_message=f"暂不支持的任务类型: {job.job_type}",
                    finished_at=finished_at,
                )
                session.commit()
                failed_count += 1
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                failed_job = session.get(ExperimentMaintenanceJob, job_pk)
                if failed_job is not None:
                    retry_scheduled_at = _calculate_retry_scheduled_at(
                        attempt_count=int(failed_job.attempt_count or 0),
                        base_backoff_seconds=settings.maintenance_job_retry_backoff_seconds,
                    )
                    # RQ 只负责唤醒 worker；具体重试节奏由数据库里的 scheduled_at 控制。
                    if int(failed_job.attempt_count or 0) < max(settings.maintenance_job_max_retry_attempts, 1):
                        repository.mark_retry_pending(
                            failed_job,
                            error_message=str(exc),
                            scheduled_at=retry_scheduled_at,
                        )
                        session.commit()
                        logger.warning(
                            "维护任务执行失败，已重新入队等待重试: job_id=%s job_type=%s attempt_count=%s next_scheduled_at=%s error=%s",
                            job_pk,
                            failed_job.job_type.value,
                            failed_job.attempt_count,
                            retry_scheduled_at,
                            exc,
                        )
                        retried_count += 1
                        continue

                    repository.mark_failed(
                        failed_job,
                        error_message=str(exc),
                        finished_at=datetime.now(timezone.utc),
                    )
                    session.commit()
                logger.exception("维护任务执行失败: job_id=%s error=%s", job_pk, exc)
                failed_count += 1
            finally:
                logger.info(
                    "维护任务退出执行上下文: experiment_id=%s maintenance_job_id=%s job_type=%s request_id=%s",
                    experiment_id,
                    job_pk,
                    job.job_type.value,
                    request_id,
                )
                reset_request_context(tokens)

    if not job_ids:
        logger.info("当前没有待执行的维护任务")
        return MaintenanceJobExecutionResult(
            processed_count=0,
            succeeded_count=0,
            failed_count=0,
            skipped_count=0,
            retried_count=0,
            job_ids=[],
        )

    processed_count = succeeded_count + failed_count + skipped_count + retried_count
    logger.info(
        "维护任务执行完成: processed=%s succeeded=%s failed=%s skipped=%s retried=%s",
        processed_count,
        succeeded_count,
        failed_count,
        skipped_count,
        retried_count,
    )
    return MaintenanceJobExecutionResult(
        processed_count=processed_count,
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        retried_count=retried_count,
        job_ids=job_ids,
    )


def refresh_experiment_analytics(
    session: Session,
    *,
    experiment_id: str,
) -> ExperimentAnalyticsRefreshResult:
    """按实验重建分钟聚合结果。

    清理原始运行数据前必须先运行本任务，把决策、轨迹、人物和采样轨迹聚合补齐。
    这样 runtime_data_cleanup 删除原始表后，stats/trajectories/heatmap 仍有轻量数据可读。
    """

    # 这里使用局部导入，避免与 experiments.service 形成循环依赖。
    from backend.app.application.analytics.aggregation import (
        bucket_start_ms,
        refresh_agent_metrics_for_times,
        refresh_decision_metrics_for_times,
        refresh_trajectory_metrics_for_times,
        refresh_trajectory_samples_for_agents,
    )
    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    _ensure_experiment_cleanup_allowed(experiment)

    decision_time_values = _list_simulation_times(
        session,
        model=AgentDecision,
        experiment_pk=experiment.id,
    )
    trajectory_time_values = _list_simulation_times(
        session,
        model=TrajectoryPoint,
        experiment_pk=experiment.id,
    )
    agent_metric_time_values = sorted({*decision_time_values, *trajectory_time_values})

    if decision_time_values:
        refresh_decision_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=decision_time_values,
        )
    if trajectory_time_values:
        refresh_trajectory_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=trajectory_time_values,
        )
    if agent_metric_time_values:
        refresh_agent_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=agent_metric_time_values,
        )
    if trajectory_time_values:
        refresh_trajectory_samples_for_agents(
            session,
            experiment_pk=experiment.id,
            agent_ids=[
                str(agent_id)
                for agent_id in session.scalars(
                    select(TrajectoryPoint.agent_id)
                    .where(TrajectoryPoint.experiment_id == experiment.id)
                    .distinct()
                    .order_by(TrajectoryPoint.agent_id)
                )
            ],
            simulation_time_ms_values=trajectory_time_values,
        )

    refreshed_decision_buckets = len({bucket_start_ms(value) for value in decision_time_values})
    refreshed_trajectory_buckets = len({bucket_start_ms(value) for value in trajectory_time_values})
    logger.info(
        "实验分钟聚合刷新完成: experiment_id=%s decision_time_count=%s trajectory_time_count=%s decision_buckets=%s trajectory_buckets=%s",
        experiment.experiment_id,
        len(decision_time_values),
        len(trajectory_time_values),
        refreshed_decision_buckets,
        refreshed_trajectory_buckets,
    )
    return ExperimentAnalyticsRefreshResult(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        decision_time_count=len(decision_time_values),
        trajectory_time_count=len(trajectory_time_values),
        refreshed_decision_buckets=refreshed_decision_buckets,
        refreshed_trajectory_buckets=refreshed_trajectory_buckets,
    )


def prewarm_experiment_heatmaps(
    session: Session,
    *,
    experiment_id: str,
) -> ExperimentHeatmapPrewarmResult:
    """按实验生成默认热力图缓存。

    这是 maintenance 层对 analytics 热力图预热能力的薄封装，用统一 dataclass 返回结果，
    方便维护任务写入 result_json。
    """

    from backend.app.application.analytics.service import prewarm_experiment_heatmaps as prewarm_service

    result = prewarm_service(session, experiment_id=experiment_id)
    return ExperimentHeatmapPrewarmResult(
        experiment_id=str(result["experiment_id"]),
        prewarmed_count=int(result["prewarmed_count"]),
        cache_hit_count=int(result["cache_hit_count"]),
        time_start_ms=int(result["time_start_ms"]) if result["time_start_ms"] is not None else None,
        time_end_ms=int(result["time_end_ms"]) if result["time_end_ms"] is not None else None,
        grid_config=dict(result["grid_config"]) if result["grid_config"] is not None else None,
        crowd_types=[str(item) for item in list(result["crowd_types"])],
        requests=[dict(item) for item in list(result["requests"])],
    )


def cleanup_experiment_runtime_data(
    session: Session,
    *,
    experiment_id: str,
    purge_heatmap_cache: bool = True,
) -> ExperimentRuntimeCleanupResult:
    """按实验安全清理原始运行数据，保留实验主记录与聚合结果。

    该操作只允许在非运行实验上执行，并且通过 summary_json.runtime_data_cleanup 保证幂等。
    真正删除前会强制刷新聚合，删除后会记录每张运行表的删除数量和保留表列表。
    """

    # 这里使用局部导入，避免与 experiments.service 形成循环依赖。
    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    _ensure_experiment_cleanup_allowed(experiment)

    existing_cleanup = (experiment.summary_json or {}).get("runtime_data_cleanup")
    if isinstance(existing_cleanup, dict):
        logger.info(
            "实验原始数据已清理，直接返回现有记录: experiment_id=%s cleaned_at_ms=%s",
            experiment.experiment_id,
            existing_cleanup.get("cleaned_at_ms"),
        )
        return ExperimentRuntimeCleanupResult(
            experiment_id=experiment.experiment_id,
            status=experiment.status.value,
            already_cleaned=True,
            cleaned_at_ms=int(existing_cleanup.get("cleaned_at_ms") or 0),
            deleted_counts={
                str(key): int(value)
                for key, value in dict(existing_cleanup.get("deleted_counts") or {}).items()
            },
            preserved_tables=list(existing_cleanup.get("preserved_tables") or _default_preserved_tables()),
            warnings=list(existing_cleanup.get("warnings") or _build_cleanup_warnings(purge_heatmap_cache)),
        )

    analytics_refresh_result = refresh_experiment_analytics(session, experiment_id=experiment_id)
    cleaned_at_ms = int(time() * 1000)
    deleted_counts = _delete_runtime_rows(
        session,
        experiment_pk=experiment.id,
        purge_heatmap_cache=purge_heatmap_cache,
    )
    preserved_tables = _default_preserved_tables()
    warnings = _build_cleanup_warnings(purge_heatmap_cache)

    # 清理后保留实验主记录和聚合结果，并把操作元数据写回 summary_json，方便运维追踪。
    summary = dict(experiment.summary_json or {})
    summary["runtime_data_cleanup"] = {
        "cleaned_at_ms": cleaned_at_ms,
        "deleted_counts": deleted_counts,
        "preserved_tables": preserved_tables,
        "warnings": warnings,
        "analytics_refresh": analytics_refresh_result.as_dict(),
    }
    experiment.summary_json = summary
    session.commit()
    session.refresh(experiment)

    logger.info(
        "实验原始运行数据清理完成: experiment_id=%s deleted_counts=%s purge_heatmap_cache=%s",
        experiment.experiment_id,
        deleted_counts,
        purge_heatmap_cache,
    )
    return ExperimentRuntimeCleanupResult(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        already_cleaned=False,
        cleaned_at_ms=cleaned_at_ms,
        deleted_counts=deleted_counts,
        preserved_tables=preserved_tables,
        warnings=warnings,
    )


def archive_experiment(
    session: Session,
    *,
    experiment_id: str,
) -> ExperimentArchiveResult:
    """按实验生成自包含归档包，并写入归档表与 experiments.summary_json.archive。

    归档包保存实验主信息、拓扑、分配、出生点、热力图缓存和维护任务状态。
    如果清理任务已经删除原始运行明细，归档会记录 raw_runtime_retained=false 和各表剩余数量，
    让后续排查能区分“没有数据”和“数据已按策略清理”。
    """

    from backend.app.application.experiments.service import get_experiment_or_404

    experiment = get_experiment_or_404(session, experiment_id)
    _ensure_experiment_cleanup_allowed(experiment)
    archive_repository = ExperimentArchiveRepository(session)

    existing_archive = (experiment.summary_json or {}).get("archive")
    if isinstance(existing_archive, dict):
        logger.info(
            "实验归档快照已存在，直接返回现有结果: experiment_id=%s archived_at_ms=%s",
            experiment.experiment_id,
            existing_archive.get("archived_at_ms"),
        )
        return _archive_result_from_snapshot(
            experiment=experiment,
            snapshot=existing_archive,
            already_archived=True,
        )

    existing_archive_record = archive_repository.get_by_experiment_id(experiment.experiment_id)
    if existing_archive_record is not None:
        archive_snapshot = dict(existing_archive_record.summary_json or {})
        summary = dict(experiment.summary_json or {})
        summary["archive"] = archive_snapshot
        experiment.summary_json = summary
        session.commit()
        session.refresh(experiment)
        logger.info(
            "实验归档表记录已存在，回填实验摘要: experiment_id=%s archive_id=%s",
            experiment.experiment_id,
            existing_archive_record.id,
        )
        return _archive_result_from_snapshot(
            experiment=experiment,
            snapshot=archive_snapshot,
            already_archived=True,
        )

    cleanup_snapshot_present = isinstance((experiment.summary_json or {}).get("runtime_data_cleanup"), dict)
    raw_runtime_remaining_counts = {
        "decision_batches": _count_rows_by_experiment_id(session, model=DecisionBatch, experiment_pk=experiment.id),
        "agent_decisions": _count_rows_by_experiment_id(session, model=AgentDecision, experiment_pk=experiment.id),
        "trajectory_batches": _count_rows_by_experiment_id(session, model=TrajectoryBatch, experiment_pk=experiment.id),
        "trajectory_points": _count_rows_by_experiment_id(session, model=TrajectoryPoint, experiment_pk=experiment.id),
    }
    raw_runtime_retained = any(count > 0 for count in raw_runtime_remaining_counts.values())
    aggregate_snapshot = {
        "zone_metric_buckets": _count_rows_by_experiment_id(session, model=ZoneMetric1m, experiment_pk=experiment.id),
        "exit_metric_buckets": _count_rows_by_experiment_id(session, model=ExitMetric1m, experiment_pk=experiment.id),
        "decision_metric_buckets": _count_rows_by_experiment_id(session, model=DecisionMetric1m, experiment_pk=experiment.id),
        "agent_metric_buckets": _count_rows_by_experiment_id(session, model=AgentMetric1m, experiment_pk=experiment.id),
        "trajectory_sampled_points": _count_rows_by_experiment_id(
            session,
            model=TrajectoryPointSampled1s,
            experiment_pk=experiment.id,
        ),
    }
    heatmap_cache_count = _count_rows_by_experiment_id(session, model=HeatmapCache, experiment_pk=experiment.id)
    maintenance_job_statuses = _collect_maintenance_job_statuses(session, experiment_pk=experiment.id)
    maintenance_job_statuses[MaintenanceJobType.EXPERIMENT_ARCHIVE.value] = MaintenanceJobStatus.SUCCEEDED.value
    warnings = _build_archive_warnings(
        cleanup_snapshot_present=cleanup_snapshot_present,
        raw_runtime_retained=raw_runtime_retained,
        heatmap_cache_count=heatmap_cache_count,
    )
    archived_at_ms = int(time() * 1000)

    archive_snapshot = {
        "archive_id": None,
        "archive_version": EXPERIMENT_ARCHIVE_VERSION,
        "archived_at_ms": archived_at_ms,
        "cleanup_snapshot_present": cleanup_snapshot_present,
        "raw_runtime_retained": raw_runtime_retained,
        "raw_runtime_remaining_counts": raw_runtime_remaining_counts,
        "aggregate_snapshot": aggregate_snapshot,
        "heatmap_cache_count": heatmap_cache_count,
        "maintenance_job_statuses": maintenance_job_statuses,
        "warnings": warnings,
    }
    archive_payload = _build_experiment_archive_payload(
        session,
        experiment=experiment,
        archive_snapshot=archive_snapshot,
    )
    archive_record = archive_repository.add(
        ExperimentArchive(
            experiment_id=experiment.experiment_id,
            experiment_name=experiment.name,
            source_status=experiment.status.value,
            archived_at_ms=archived_at_ms,
            archive_version=EXPERIMENT_ARCHIVE_VERSION,
            archive_payload_json=archive_payload,
            summary_json=archive_snapshot,
            raw_runtime_retained=raw_runtime_retained,
            archive_location=None,
        )
    )
    archive_snapshot["archive_id"] = int(archive_record.id)
    archive_record.summary_json = dict(archive_snapshot)
    archive_record.archive_payload_json = {
        **archive_payload,
        "archive_id": int(archive_record.id),
    }
    flag_modified(archive_record, "summary_json")
    flag_modified(archive_record, "archive_payload_json")
    summary = dict(experiment.summary_json or {})
    summary["archive"] = archive_snapshot
    experiment.summary_json = summary
    session.commit()
    session.refresh(experiment)

    logger.info(
        "实验归档包写入完成: experiment_id=%s archive_id=%s raw_runtime_retained=%s heatmap_cache_count=%s",
        experiment.experiment_id,
        archive_record.id,
        raw_runtime_retained,
        heatmap_cache_count,
    )
    return ExperimentArchiveResult(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        already_archived=False,
        archived_at_ms=archived_at_ms,
        cleanup_snapshot_present=cleanup_snapshot_present,
        raw_runtime_retained=raw_runtime_retained,
        raw_runtime_remaining_counts=raw_runtime_remaining_counts,
        aggregate_snapshot=aggregate_snapshot,
        heatmap_cache_count=heatmap_cache_count,
        maintenance_job_statuses=maintenance_job_statuses,
        warnings=warnings,
    )


def _archive_result_from_snapshot(
    *,
    experiment: Experiment,
    snapshot: dict[str, object],
    already_archived: bool,
) -> ExperimentArchiveResult:
    """从轻量归档摘要恢复归档任务返回值。"""

    return ExperimentArchiveResult(
        experiment_id=experiment.experiment_id,
        status=experiment.status.value,
        already_archived=already_archived,
        archived_at_ms=int(snapshot.get("archived_at_ms") or 0),
        cleanup_snapshot_present=bool(snapshot.get("cleanup_snapshot_present")),
        raw_runtime_retained=bool(snapshot.get("raw_runtime_retained")),
        raw_runtime_remaining_counts={
            str(key): int(value)
            for key, value in dict(snapshot.get("raw_runtime_remaining_counts") or {}).items()
        },
        aggregate_snapshot={
            str(key): int(value)
            for key, value in dict(snapshot.get("aggregate_snapshot") or {}).items()
        },
        heatmap_cache_count=int(snapshot.get("heatmap_cache_count") or 0),
        maintenance_job_statuses={
            str(key): str(value)
            for key, value in dict(snapshot.get("maintenance_job_statuses") or {}).items()
        },
        warnings=list(snapshot.get("warnings") or []),
    )


def _build_experiment_archive_payload(
    session: Session,
    *,
    experiment: Experiment,
    archive_snapshot: dict[str, object],
) -> dict[str, object]:
    """生成不依赖在线模板表的实验归档包。"""

    allocations = _list_archive_allocations(session, experiment_pk=experiment.id)
    template_snapshots = {
        str(allocation["type_id"]): dict(allocation.get("template_snapshot") or {})
        for allocation in allocations
    }
    return {
        "archive_version": EXPERIMENT_ARCHIVE_VERSION,
        "archive_id": archive_snapshot.get("archive_id"),
        "archive_summary": dict(archive_snapshot),
        "experiment_snapshot": _experiment_archive_dict(experiment),
        "topology_snapshot": {
            "zones": _list_archive_zones(session, experiment_pk=experiment.id),
            "exits": _list_archive_exits(session, experiment_pk=experiment.id),
        },
        "allocation_snapshot": {
            "groups": allocations,
            "crowd_type_snapshots": template_snapshots,
        },
        "spawn_agents_snapshot": _list_archive_agents(session, experiment_pk=experiment.id),
        "maintenance_summary": {
            "jobs": _list_archive_maintenance_jobs(session, experiment_pk=experiment.id),
        },
        "analytics_summary": {
            "heatmap_cache": _list_archive_heatmap_cache(session, experiment_pk=experiment.id),
            "aggregate_counts": dict(archive_snapshot.get("aggregate_snapshot") or {}),
        },
        "runtime_summary": {
            "raw_runtime_remaining_counts": dict(archive_snapshot.get("raw_runtime_remaining_counts") or {}),
            "raw_runtime_retained": bool(archive_snapshot.get("raw_runtime_retained")),
        },
    }


def _experiment_archive_dict(experiment: Experiment) -> dict[str, object]:
    return _json_safe(
        {
            "experiment_id": experiment.experiment_id,
            "name": experiment.name,
            "description": experiment.description,
            "map_id": experiment.map_id,
            "status": experiment.status,
            "llm_config": {
                "model": experiment.llm_model,
                "temperature": experiment.llm_temperature,
                "max_tokens": experiment.llm_max_tokens,
                "timeout_ms": experiment.llm_timeout_ms,
            },
            "decision_config": {
                "batch_interval_ms": experiment.decision_batch_interval_ms,
                "batch_size": experiment.decision_batch_size,
                "fallback_to_algorithm": experiment.fallback_to_algorithm,
            },
            "tags": list(experiment.tags or []),
            "agent_id_prefix": experiment.agent_id_prefix,
            "simulation_started_at_ms": experiment.simulation_started_at_ms,
            "simulation_completed_at_ms": experiment.simulation_completed_at_ms,
            "actual_duration_ms": experiment.actual_duration_ms,
            "completion_reason": experiment.completion_reason,
            "final_agent_count": experiment.final_agent_count,
            "summary": dict(experiment.summary_json or {}),
            "created_at_ms": datetime_to_timestamp_ms(experiment.created_at),
            "updated_at_ms": datetime_to_timestamp_ms(experiment.updated_at),
        }
    )


def _list_archive_zones(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(ExperimentZone)
        .where(ExperimentZone.experiment_id == experiment_pk)
        .order_by(ExperimentZone.id)
    ).all()
    return [
        _json_safe(
            {
                "zone_id": row.zone_id,
                "zone_name": row.zone_name,
                "zone_type": row.zone_type,
                "bounds": {
                    "min_x": row.min_x,
                    "min_z": row.min_z,
                    "max_x": row.max_x,
                    "max_z": row.max_z,
                },
                "capacity": row.capacity,
                "is_entry_zone": row.is_entry_zone,
                "metadata": dict(row.metadata_json or {}),
            }
        )
        for row in rows
    ]


def _list_archive_exits(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(ExperimentExit)
        .where(ExperimentExit.experiment_id == experiment_pk)
        .order_by(ExperimentExit.id)
    ).all()
    return [
        _json_safe(
            {
                "exit_id": row.exit_id,
                "source_zone_id": row.source_zone_id,
                "target_zone_id": row.target_zone_id,
                "exit_type": row.exit_type,
                "position": {
                    "x": row.position_x,
                    "z": row.position_z,
                },
                "pass_capacity_per_sec": row.pass_capacity_per_sec,
                "metadata": dict(row.metadata_json or {}),
            }
        )
        for row in rows
    ]


def _list_archive_allocations(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(ExperimentAllocation)
        .where(ExperimentAllocation.experiment_id == experiment_pk)
        .order_by(ExperimentAllocation.id)
    ).all()
    return [
        _json_safe(
            {
                "type_id": row.type_id,
                "count": row.count,
                "distribution_mode": row.distribution_mode,
                "distribution_config": dict(row.distribution_config or {}),
                "template_snapshot": dict(row.template_snapshot or {}),
            }
        )
        for row in rows
    ]


def _list_archive_agents(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(ExperimentAgent)
        .where(ExperimentAgent.experiment_id == experiment_pk)
        .order_by(ExperimentAgent.spawn_order, ExperimentAgent.id)
    ).all()
    return [
        _json_safe(
            {
                "agent_id": row.agent_id,
                "crowd_type": row.crowd_type,
                "spawn_zone_id": row.spawn_zone_id,
                "position": {
                    "x": row.spawn_x,
                    "z": row.spawn_z,
                },
                "rotation_y": row.rotation_y,
                "spawn_order": row.spawn_order,
                "generation_seed": row.generation_seed,
            }
        )
        for row in rows
    ]


def _list_archive_heatmap_cache(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(HeatmapCache)
        .where(HeatmapCache.experiment_id == experiment_pk)
        .order_by(HeatmapCache.created_at, HeatmapCache.cache_key)
    ).all()
    return [
        _json_safe(
            {
                "cache_key": row.cache_key,
                "metric": row.metric,
                "time_start_ms": row.time_start_ms,
                "time_end_ms": row.time_end_ms,
                "crowd_type_filter": row.crowd_type_filter,
                "zone_id_filter": row.zone_id_filter,
                "normalization": row.normalization,
                "grid_hash": row.grid_hash,
                "statistics": dict(row.statistics_json or {}),
                "cell_count": len(row.cells or []),
                "expires_at_ms": datetime_to_timestamp_ms(row.expires_at) if row.expires_at is not None else None,
            }
        )
        for row in rows
    ]


def _list_archive_maintenance_jobs(session: Session, *, experiment_pk: int) -> list[dict[str, object]]:
    rows = session.scalars(
        select(ExperimentMaintenanceJob)
        .where(ExperimentMaintenanceJob.experiment_id == experiment_pk)
        .order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
    ).all()
    return [
        _json_safe(
            {
                "job_id": row.id,
                "job_type": row.job_type,
                "status": row.status,
                "attempt_count": row.attempt_count,
                "scheduled_at_ms": datetime_to_timestamp_ms(row.scheduled_at) if row.scheduled_at is not None else None,
                "started_at_ms": datetime_to_timestamp_ms(row.started_at) if row.started_at is not None else None,
                "finished_at_ms": datetime_to_timestamp_ms(row.finished_at) if row.finished_at is not None else None,
                "error_message": row.error_message,
                "payload": dict(row.payload_json or {}),
                "result": dict(row.result_json or {}),
            }
        )
        for row in rows
    ]


def _json_safe(value: object) -> object:
    """把 ORM 数值转换为 JSONB 可安全写入的值。"""

    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        return datetime_to_timestamp_ms(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, tuple):
        return [_json_safe(item) for item in value]
    return value


def _ensure_experiment_cleanup_allowed(experiment: Experiment) -> None:
    if experiment.status not in {ExperimentStatus.COMPLETED, ExperimentStatus.FAILED}:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态为 `{experiment.status}`，不允许执行原始运行数据清理",
            data={"status": experiment.status},
        )


def _delete_runtime_rows(
    session: Session,
    *,
    experiment_pk: int,
    purge_heatmap_cache: bool,
) -> dict[str, int]:
    deleted_counts: dict[str, int] = {}

    # 先删明细再删批次，确保外键约束顺序正确。
    for table_name, model in (
        ("agent_decisions", AgentDecision),
        ("decision_batches", DecisionBatch),
        ("trajectory_points", TrajectoryPoint),
        ("trajectory_batches", TrajectoryBatch),
    ):
        deleted_counts[table_name] = _delete_rows_by_experiment_id(
            session,
            model=model,
            experiment_pk=experiment_pk,
        )

    # 热力图缓存依赖原始轨迹点；清理原始数据后继续保留旧缓存容易误导调用方，因此默认一并清掉。
    deleted_counts["heatmap_cache"] = (
        _delete_rows_by_experiment_id(session, model=HeatmapCache, experiment_pk=experiment_pk)
        if purge_heatmap_cache
        else 0
    )
    if purge_heatmap_cache:
        redis_deleted = get_redis_client().delete_by_pattern(
            "heatmap",
            "response",
            "exp",
            str(experiment_pk),
            "*",
        )
        if redis_deleted > 0:
            logger.info(
                "实验热力图 Redis 热点缓存已清理: experiment_pk=%s deleted=%s",
                experiment_pk,
                redis_deleted,
            )
    return deleted_counts


def _delete_rows_by_experiment_id(
    session: Session,
    *,
    model: type[object],
    experiment_pk: int,
) -> int:
    count = _count_rows_by_experiment_id(session, model=model, experiment_pk=experiment_pk)
    if count <= 0:
        return 0
    session.execute(delete(model).where(getattr(model, "experiment_id") == experiment_pk))
    return count


def _count_rows_by_experiment_id(
    session: Session,
    *,
    model: type[object],
    experiment_pk: int,
) -> int:
    count_stmt: Select[tuple[int]] = (
        select(func.count())
        .select_from(model)
        .where(getattr(model, "experiment_id") == experiment_pk)
    )
    return int(session.scalar(count_stmt) or 0)


def _list_simulation_times(
    session: Session,
    *,
    model: type[object],
    experiment_pk: int,
) -> list[int]:
    stmt = (
        select(getattr(model, "simulation_time_ms"))
        .where(getattr(model, "experiment_id") == experiment_pk)
        .distinct()
        .order_by(getattr(model, "simulation_time_ms"))
    )
    return [int(value) for value in session.scalars(stmt) if value is not None]


def _default_preserved_tables() -> list[str]:
    return [
        "experiments",
        "experiment_archives",
        "experiment_maintenance_jobs",
        "crowd_type_templates",
        "experiment_zones",
        "experiment_exits",
        "experiment_allocations",
        "experiment_agents",
        "zone_metrics_1m",
        "exit_metrics_1m",
        "decision_metrics_1m",
    ]


def _build_cleanup_warnings(purge_heatmap_cache: bool) -> list[str]:
    warnings = [
        "原始轨迹点、轨迹批次、决策明细、决策批次已删除，轨迹查询与路径追踪接口将不再返回历史明细。",
        "实验主记录、启动前配置和分钟聚合结果会保留，可继续用于实验概览与聚合统计。",
    ]
    if purge_heatmap_cache:
        warnings.insert(
            1,
            "热力图缓存已一并清理；后续热力图请求在缺少原始轨迹点的情况下将无法回源重算。",
        )
    else:
        warnings.insert(
            1,
            "热力图缓存已保留，后续仅能命中已预热或已有缓存的请求组合；未命中的热力图请求将直接返回冲突错误。",
        )
    return warnings


def _collect_maintenance_job_statuses(session: Session, *, experiment_pk: int) -> dict[str, str]:
    stmt = (
        select(ExperimentMaintenanceJob)
        .where(ExperimentMaintenanceJob.experiment_id == experiment_pk)
        .order_by(ExperimentMaintenanceJob.created_at.desc(), ExperimentMaintenanceJob.id.desc())
    )
    status_map: dict[str, str] = {}
    for job in session.scalars(stmt):
        job_type = str(job.job_type.value)
        if job_type in status_map:
            continue
        status_map[job_type] = str(job.status.value)
    return status_map


def _build_job_record(
    *,
    experiment_id: str,
    job: ExperimentMaintenanceJob,
    include_payload: bool,
    include_result: bool,
) -> MaintenanceJobRecord:
    """把 ORM 任务对象转换为结构化查询结果。"""

    return MaintenanceJobRecord(
        job_id=int(job.id),
        experiment_id=experiment_id,
        job_type=job.job_type.value,
        status=job.status.value,
        attempt_count=int(job.attempt_count or 0),
        created_at_ms=datetime_to_timestamp_ms(job.created_at),
        updated_at_ms=datetime_to_timestamp_ms(job.updated_at),
        scheduled_at_ms=datetime_to_timestamp_ms(job.scheduled_at) if job.scheduled_at is not None else None,
        started_at_ms=datetime_to_timestamp_ms(job.started_at) if job.started_at is not None else None,
        finished_at_ms=datetime_to_timestamp_ms(job.finished_at) if job.finished_at is not None else None,
        error_message=job.error_message,
        payload_json=dict(job.payload_json or {}) if include_payload else None,
        result_json=dict(job.result_json or {}) if include_result else None,
    )


def _clear_maintenance_summary_keys(
    experiment: Experiment,
    *,
    selected_job_types: set[MaintenanceJobType],
) -> list[str]:
    """根据重放任务类型清理会影响幂等判断的实验摘要快照。"""

    summary = dict(experiment.summary_json or {})
    cleared_keys: list[str] = []
    if MaintenanceJobType.RUNTIME_DATA_CLEANUP in selected_job_types and "runtime_data_cleanup" in summary:
        summary.pop("runtime_data_cleanup", None)
        cleared_keys.append("runtime_data_cleanup")
    if MaintenanceJobType.EXPERIMENT_ARCHIVE in selected_job_types and "archive" in summary:
        summary.pop("archive", None)
        cleared_keys.append("archive")
    if cleared_keys:
        experiment.summary_json = summary
    return cleared_keys


def _dispatch_maintenance_jobs_after_mutation(*, dispatch: bool) -> dict[str, object] | None:
    """在手工重置或重放之后补派发 RQ 调度任务。"""

    if not dispatch:
        return None

    from backend.app.infrastructure.workers.rq_worker import dispatch_pending_maintenance_jobs

    dispatch_result = dispatch_pending_maintenance_jobs(limit=20, fanout=1)
    return dispatch_result.as_dict()


def _build_maintenance_job_conflict(message: str, data: dict[str, object] | None = None) -> AppError:
    """构造维护任务冲突异常。"""

    return AppError(
        message=message,
        code=error_codes.EXPERIMENT_STATUS_CONFLICT,
        status_code=409,
        data=data or {},
    )


def _build_archive_warnings(
    *,
    cleanup_snapshot_present: bool,
    raw_runtime_retained: bool,
    heatmap_cache_count: int,
) -> list[str]:
    warnings: list[str] = []
    if not cleanup_snapshot_present:
        warnings.append("归档时未检测到 runtime_data_cleanup 快照，说明清理链路可能尚未完整执行。")
    if raw_runtime_retained:
        warnings.append("归档时仍检测到原始运行数据残留，建议补跑清理任务后再重新归档。")
    if heatmap_cache_count <= 0:
        warnings.append("归档时未保留热力图缓存，后续热力图请求可能只能返回缓存缺失错误。")
    return warnings


def _get_experiment_external_id(session: Session, *, experiment_pk: int) -> str:
    stmt = select(Experiment.experiment_id).where(Experiment.id == experiment_pk)
    experiment_id = session.scalar(stmt)
    if experiment_id is None:
        raise RuntimeError(f"实验主键 `{experiment_pk}` 对应的实验不存在")
    return str(experiment_id)


def _calculate_retry_scheduled_at(*, attempt_count: int, base_backoff_seconds: float) -> datetime:
    """按尝试次数生成下一次重试时间，采用简单指数退避。"""

    effective_base = max(base_backoff_seconds, 0.0)
    if effective_base <= 0:
        return datetime.now(timezone.utc)

    exponent = max(attempt_count - 1, 0)
    delay_seconds = effective_base * (2**exponent)
    return datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
