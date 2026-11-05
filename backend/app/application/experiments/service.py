"""实验生命周期应用服务。

本文件负责实验主状态机的编排：创建、启动、完成、失败。
其他服务会复用这里的 `get_experiment_or_404`、`ensure_experiment_editable`
和启动前状态刷新逻辑，确保所有入口都按同一套状态约束执行。
"""

from __future__ import annotations

from collections import Counter
from time import time
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.app.application.common import (
    datetime_to_timestamp_ms,
    raise_experiment_not_found,
    raise_experiment_status_conflict,
)
from backend.app.domain.enums import CompletionReason, DecisionSource, ExperimentStatus, ZoneType
from backend.app.infrastructure.db.models import Experiment
from backend.app.infrastructure.db.repositories import (
    AgentDecisionRepository,
    DecisionBatchRepository,
    ExperimentAllocationRepository,
    ExperimentZoneRepository,
    ExperimentRepository,
    TrajectoryPointRepository,
)
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.experiments import (
    CrowdTypeCompletionStat,
    ExperimentCompletedData,
    ExperimentCompleteRequest,
    ExperimentCompletionSummary,
    ExperimentCreateRequest,
    ExperimentCreatedData,
    ExperimentFailedData,
    ExperimentFailRequest,
    ExperimentFailureSummary,
    ExperimentStartRequest,
    ExperimentStartedData,
)

EDITABLE_EXPERIMENT_STATUSES = {ExperimentStatus.INITIALIZED}
FAILABLE_EXPERIMENT_STATUSES = {
    ExperimentStatus.INITIALIZED,
    ExperimentStatus.CONFIGURED,
    ExperimentStatus.RUNNING,
}
logger = get_logger(__name__)


def create_experiment(session: Session, payload: ExperimentCreateRequest) -> ExperimentCreatedData:
    """创建实验并写入数据库。

    创建时只保存实验元信息和 LLM/决策配置，不创建拓扑、分配或运行期数据。
    实验初始状态固定为 `initialized`，等待拓扑和人群分配补齐后再自动转为
    `configured`。
    """

    repository = ExperimentRepository(session)
    experiment = Experiment(
        experiment_id=_generate_experiment_id(),
        name=payload.name,
        description=payload.description,
        map_id=payload.map_id,
        status=ExperimentStatus.INITIALIZED,
        llm_model=payload.llm_config.model,
        llm_temperature=payload.llm_config.temperature,
        llm_max_tokens=payload.llm_config.max_tokens,
        llm_timeout_ms=payload.llm_config.timeout_ms,
        decision_batch_interval_ms=payload.decision_config.batch_interval_ms,
        decision_batch_size=payload.decision_config.batch_size,
        fallback_to_algorithm=payload.decision_config.fallback_to_algorithm,
        tags=[tag for tag in payload.tags if tag],
    )
    repository.add(experiment)
    session.commit()
    session.refresh(experiment)
    logger.info("实验创建完成: experiment_id=%s name=%s", experiment.experiment_id, experiment.name)
    return ExperimentCreatedData(
        experiment_id=experiment.experiment_id,
        status=experiment.status,
        created_at_ms=datetime_to_timestamp_ms(experiment.created_at),
    )


def get_experiment_or_404(session: Session, experiment_id: str) -> Experiment:
    """查询实验，不存在则抛错。"""

    repository = ExperimentRepository(session)
    experiment = repository.get_by_experiment_id(experiment_id)
    if experiment is None:
        raise_experiment_not_found(f"实验 `{experiment_id}` 不存在")
    return experiment


def ensure_experiment_editable(experiment: Experiment) -> None:
    """确保实验仍允许修改启动前配置。"""

    if experiment.status not in EDITABLE_EXPERIMENT_STATUSES:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态不允许修改启动前配置",
            data={"status": experiment.status},
        )


def refresh_pre_run_status(session: Session, experiment: Experiment) -> ExperimentStatus:
    """根据实验当前配置情况刷新启动前状态。

    启动前只关心两个里程碑：拓扑是否存在、人群分配是否存在。
    出生点可以重复生成或按 seed 幂等回放，因此不参与 `configured` 判定。
    """

    if experiment.status not in EDITABLE_EXPERIMENT_STATUSES:
        return experiment.status

    repository = ExperimentRepository(session)
    # 启动前状态只由“是否已有拓扑”和“是否已有人群分配”这两个里程碑决定。
    has_topology = repository.has_topology(experiment.id)
    has_allocations = repository.has_allocations(experiment.id)

    experiment.status = (
        ExperimentStatus.CONFIGURED
        if has_topology and has_allocations
        else ExperimentStatus.INITIALIZED
    )
    session.flush()
    return experiment.status


def start_experiment(
    session: Session,
    *,
    experiment_id: str,
    payload: ExperimentStartRequest | None,
) -> ExperimentStartedData:
    """将实验从 `configured` 切换到 `running`。

    只有拓扑与人群分配都完成的实验才能启动。启动后，拓扑和分配不再允许修改，
    运行期接口开始接受决策批次和轨迹批次。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    if experiment.status != ExperimentStatus.CONFIGURED:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态为 `{experiment.status}`，无法启动",
            data={"status": experiment.status},
        )

    # 如果调用方没有显式传入开始时间，就用服务端当前时间兜底。
    started_at_ms = (
        payload.actual_start_time_ms
        if payload is not None and payload.actual_start_time_ms is not None
        else int(time() * 1000)
    )

    experiment.status = ExperimentStatus.RUNNING
    experiment.simulation_started_at_ms = started_at_ms
    session.commit()
    session.refresh(experiment)
    logger.info("实验启动成功: experiment_id=%s started_at_ms=%s", experiment.experiment_id, started_at_ms)
    return ExperimentStartedData(
        experiment_id=experiment.experiment_id,
        status=experiment.status,
        started_at_ms=started_at_ms,
    )


def complete_experiment(
    session: Session,
    *,
    experiment_id: str,
    payload: ExperimentCompleteRequest,
) -> ExperimentCompletedData:
    """完成实验并写入当前阶段可得的实验总结。

    完成动作只负责同步写入“轻量摘要”和维护任务记录；聚合刷新、热力图预热、
    原始运行数据清理和归档交给 RQ worker 异步消费，避免接口请求被长任务阻塞。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    if experiment.status != ExperimentStatus.RUNNING:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态为 `{experiment.status}`，无法完成",
            data={"status": experiment.status},
        )

    completed_at_ms = _resolve_completed_at_ms(experiment, actual_duration_ms=payload.actual_duration_ms)
    summary = _build_completion_summary(
        session,
        experiment,
        actual_duration_ms=payload.actual_duration_ms,
    )

    experiment.status = ExperimentStatus.COMPLETED
    experiment.simulation_completed_at_ms = completed_at_ms
    experiment.actual_duration_ms = payload.actual_duration_ms
    experiment.completion_reason = payload.reason
    experiment.final_agent_count = payload.final_agent_count
    experiment.summary_json = summary

    # 实验完成后先写入待处理任务，真正清理动作交给 worker 命令执行。
    from backend.app.application.maintenance.service import enqueue_post_complete_jobs

    maintenance_jobs = enqueue_post_complete_jobs(
        session,
        experiment=experiment,
        purge_heatmap_cache=True,
    )
    session.commit()
    session.refresh(experiment)

    # 维护任务记录入库后，尽量立刻补派发一个 RQ 调度 job，避免必须依赖人工触发。
    if maintenance_jobs:
        from backend.app.infrastructure.workers.rq_worker import dispatch_pending_maintenance_jobs

        dispatch_result = dispatch_pending_maintenance_jobs(limit=1, fanout=1)
        logger.info(
            "实验完成后已触发维护任务派发: experiment_id=%s dispatch_status=%s rq_job_ids=%s",
            experiment.experiment_id,
            dispatch_result.status,
            dispatch_result.rq_job_ids,
        )
    logger.info(
        "实验完成: experiment_id=%s duration_ms=%s final_agent_count=%s maintenance_job_ids=%s",
        experiment.experiment_id,
        payload.actual_duration_ms,
        payload.final_agent_count,
        [job.id for job in maintenance_jobs],
    )
    return ExperimentCompletedData(
        experiment_id=experiment.experiment_id,
        status=experiment.status,
        summary=ExperimentCompletionSummary.model_validate(summary),
    )


def fail_experiment(
    session: Session,
    *,
    experiment_id: str,
    payload: ExperimentFailRequest,
) -> ExperimentFailedData:
    """将实验标记为失败，并保存失败上下文。

    失败态用于 Unity 运行时异常、人工中止或其他无法正常 complete 的情况。
    与 complete 不同，失败不会自动入队完成态维护链路，避免对不完整数据做清理归档。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    previous_status = experiment.status
    if previous_status not in FAILABLE_EXPERIMENT_STATUSES:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态为 `{experiment.status}`，无法标记失败",
            data={"status": experiment.status},
        )

    failed_at_ms = _resolve_failed_at_ms(experiment, payload=payload)
    duration_ms = _resolve_failure_duration_ms(
        experiment,
        failed_at_ms=failed_at_ms,
        actual_duration_ms=payload.actual_duration_ms,
    )
    failure_summary = ExperimentFailureSummary(
        reason=payload.reason,
        message=payload.message,
        failed_at_ms=failed_at_ms,
        duration_ms=duration_ms,
        final_agent_count=payload.final_agent_count,
        previous_status=previous_status,
        details=payload.details,
    )
    summary = dict(experiment.summary_json or {})
    summary["failure"] = failure_summary.model_dump(mode="json")

    experiment.status = ExperimentStatus.FAILED
    experiment.simulation_completed_at_ms = failed_at_ms
    experiment.actual_duration_ms = duration_ms
    experiment.completion_reason = CompletionReason.ERROR
    experiment.final_agent_count = payload.final_agent_count
    experiment.summary_json = summary

    session.commit()
    session.refresh(experiment)
    logger.warning(
        "实验已标记失败: experiment_id=%s previous_status=%s reason=%s failed_at_ms=%s",
        experiment.experiment_id,
        previous_status,
        payload.reason,
        failed_at_ms,
    )
    return ExperimentFailedData(
        experiment_id=experiment.experiment_id,
        status=experiment.status,
        failed_at_ms=failed_at_ms,
        summary=failure_summary,
    )


def _resolve_completed_at_ms(experiment: Experiment, *, actual_duration_ms: int) -> int:
    """推导实验完成时刻。

    Unity 上报的是模拟持续时长，后端优先用 `started_at + duration` 得到稳定的
    模拟完成时间；如果实验没有开始时间，才退回服务端当前时间。
    """

    if experiment.simulation_started_at_ms is not None:
        return experiment.simulation_started_at_ms + actual_duration_ms
    return int(time() * 1000)


def _resolve_failed_at_ms(experiment: Experiment, *, payload: ExperimentFailRequest) -> int:
    if payload.failed_at_ms is not None:
        return payload.failed_at_ms
    if experiment.simulation_started_at_ms is not None and payload.actual_duration_ms is not None:
        return experiment.simulation_started_at_ms + payload.actual_duration_ms
    return int(time() * 1000)


def _resolve_failure_duration_ms(
    experiment: Experiment,
    *,
    failed_at_ms: int,
    actual_duration_ms: int | None,
) -> int | None:
    if actual_duration_ms is not None:
        return actual_duration_ms
    if experiment.simulation_started_at_ms is not None:
        return max(0, failed_at_ms - experiment.simulation_started_at_ms)
    return None


def _build_completion_summary(
    session: Session,
    experiment: Experiment,
    *,
    actual_duration_ms: int,
) -> dict[str, object]:
    """构建完成态轻量摘要。

    摘要用于接口返回和后续归档索引，不替代完整的聚合表与归档包。
    这里直接读取真实持久化表，避免把接口层临时响应当作事实来源。
    """

    batch_repository = DecisionBatchRepository(session)
    decision_repository = AgentDecisionRepository(session)
    trajectory_repository = TrajectoryPointRepository(session)
    # 当前阶段直接从真实写入表聚合摘要，避免只依赖接口层缓存数据。
    return {
        "total_trajectory_points": trajectory_repository.count_by_experiment_id(experiment.id),
        "total_decision_requests": batch_repository.count_by_experiment_id(experiment.id),
        "total_llm_decisions": decision_repository.count_by_experiment_and_source(
            experiment.id,
            DecisionSource.LLM,
        ),
        "llm_fallback_count": decision_repository.count_by_experiment_and_source(
            experiment.id,
            DecisionSource.FALLBACK,
        ),
        "duration_ms": actual_duration_ms,
        "crowd_type_stats": [
            item.model_dump(mode="json") for item in _build_crowd_type_completion_stats(session, experiment)
        ],
    }


def _generate_experiment_id() -> str:
    return f"exp_{uuid4().hex[:24]}"


def _build_crowd_type_completion_stats(
    session: Session,
    experiment: Experiment,
) -> list[CrowdTypeCompletionStat]:
    """按人群类型统计完成时仍在场人数和已离场人数。

    这里用每个 agent 的最后一个轨迹点判断是否进入 `exit_final` 区域。
    如果没有轨迹或没有最终出口区域，就只返回基于分配数量的保守摘要。
    """

    allocation_repository = ExperimentAllocationRepository(session)
    allocations = allocation_repository.list_by_experiment_id(experiment.id)
    if not allocations:
        return []

    zone_repository = ExperimentZoneRepository(session)
    exit_final_zone_ids = {
        zone.zone_id
        for zone in zone_repository.list_by_experiment_id(experiment.id)
        if zone.zone_type == ZoneType.EXIT_FINAL
    }
    trajectory_repository = TrajectoryPointRepository(session)
    last_points = trajectory_repository.list_last_points_by_agent(experiment.id)

    exited_count_by_type: Counter[str] = Counter()
    for point in last_points:
        if point.zone_id in exit_final_zone_ids:
            exited_count_by_type[point.crowd_type] += 1

    stats: list[CrowdTypeCompletionStat] = []
    for allocation in allocations:
        exited_count = int(exited_count_by_type.get(allocation.type_id, 0))
        final_count = max(int(allocation.count) - exited_count, 0)
        stats.append(
            CrowdTypeCompletionStat(
                type_id=allocation.type_id,
                final_count=final_count,
                exited_count=exited_count,
            )
        )

    return stats
