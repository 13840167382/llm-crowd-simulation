"""RQ 队列辅助函数。

这个模块把数据库里的维护任务和轨迹刷新任务派发到 Redis/RQ 队列。
维护任务本身仍以 experiment_maintenance_jobs 表为事实来源，RQ job 只是“唤醒 worker 执行一轮”的载体；
这样即使 RQ job 丢失，也可以通过命令重新派发数据库中的 pending 任务。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from functools import lru_cache
import multiprocessing
from typing import Any
from uuid import uuid4

from backend.app.api import error_codes
from backend.app.api.errors import AppError
from backend.app.application.maintenance.service import run_pending_maintenance_jobs
from backend.app.core.config import get_settings
from backend.app.core.context import reset_request_context, set_request_context
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.repositories import ExperimentMaintenanceJobRepository
from backend.app.infrastructure.db.session import check_database_health, get_session_factory
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class MaintenanceDispatchResult:
    """维护任务派发结果，供命令行、API 和日志统一消费。"""

    status: str
    queue_name: str | None
    enqueued_count: int
    due_job_count: int
    next_scheduled_delay_seconds: float | None
    rq_job_ids: list[str]
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class TrajectoryRefreshDispatchResult:
    """轨迹刷新 RQ 投递结果。"""

    status: str
    queue_name: str | None
    rq_job_id: str | None
    message: str

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def dispatch_pending_maintenance_jobs(*, limit: int = 20, fanout: int = 1) -> MaintenanceDispatchResult:
    """把已经到期的数据库维护任务派发到 RQ。

    如果当前没有到期任务，但存在未来 scheduled_at 的重试任务，会入队一个延迟 dispatch job。
    到点后该 job 再调用 process_maintenance_dispatch 读取数据库 pending 任务。
    """

    queue = _get_maintenance_queue(required=False)
    if queue is None:
        return MaintenanceDispatchResult(
            status="disabled",
            queue_name=None,
            enqueued_count=0,
            due_job_count=0,
            next_scheduled_delay_seconds=None,
            rq_job_ids=[],
            message="Redis 未启用，跳过 maintenance RQ 派发",
        )

    session = get_session_factory()()
    try:
        repository = ExperimentMaintenanceJobRepository(session)
        due_jobs = repository.list_pending_jobs(limit=limit, lock_rows=False)
        next_scheduled_at = repository.get_next_pending_scheduled_at()
    finally:
        session.close()

    due_job_count = len(due_jobs)
    if due_job_count > 0:
        # fanout 控制一次派发多少个 RQ job，但真正会执行哪些维护任务仍由数据库锁决定。
        effective_fanout = min(max(fanout, 1), due_job_count)
        result = _enqueue_dispatch_jobs(queue=queue, fanout=effective_fanout, delay_seconds=None)
        if result.status == "error":
            return MaintenanceDispatchResult(
                status="error",
                queue_name=queue.name,
                enqueued_count=0,
                due_job_count=due_job_count,
                next_scheduled_delay_seconds=None,
                rq_job_ids=[],
                message=result.message,
            )
        logger.info(
            "维护任务已派发到 RQ: queue=%s due_job_count=%s enqueued_count=%s",
            queue.name,
            due_job_count,
            result.enqueued_count,
        )
        return MaintenanceDispatchResult(
            status="enqueued",
            queue_name=queue.name,
            enqueued_count=result.enqueued_count,
            due_job_count=due_job_count,
            next_scheduled_delay_seconds=None,
            rq_job_ids=result.rq_job_ids,
            message="维护任务已派发到 RQ",
        )

    delay_seconds = _calculate_delay_seconds(next_scheduled_at)
    if delay_seconds is None:
        return MaintenanceDispatchResult(
            status="empty",
            queue_name=queue.name,
            enqueued_count=0,
            due_job_count=0,
            next_scheduled_delay_seconds=None,
            rq_job_ids=[],
            message="当前没有待派发的维护任务",
        )

    result = _enqueue_dispatch_jobs(queue=queue, fanout=1, delay_seconds=delay_seconds)
    if result.status == "error":
        return MaintenanceDispatchResult(
            status="error",
            queue_name=queue.name,
            enqueued_count=0,
            due_job_count=0,
            next_scheduled_delay_seconds=delay_seconds,
            rq_job_ids=[],
            message=result.message,
        )
    logger.info(
        "维护任务已安排延迟派发: queue=%s delay_seconds=%s rq_job_ids=%s",
        queue.name,
        delay_seconds,
        result.rq_job_ids,
    )
    return MaintenanceDispatchResult(
        status="scheduled",
        queue_name=queue.name,
        enqueued_count=result.enqueued_count,
        due_job_count=0,
        next_scheduled_delay_seconds=delay_seconds,
        rq_job_ids=result.rq_job_ids,
        message="维护任务已安排延迟派发",
    )


def process_maintenance_dispatch(*, limit: int = 1) -> dict[str, Any]:
    """RQ worker 执行入口：消费一轮数据库维护任务，并尝试派发后续任务。"""

    logger.info("RQ maintenance dispatch 开始: limit=%s", limit)
    session = get_session_factory()()
    try:
        # 单个 RQ job 只跑有限数量的数据库任务，避免一个 job 长时间占用 worker。
        execution_result = run_pending_maintenance_jobs(session, limit=max(limit, 1))
    finally:
        session.close()

    try:
        follow_up_result = dispatch_pending_maintenance_jobs(limit=max(limit, 1), fanout=1)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RQ maintenance dispatch follow-up 失败: error=%s", exc)
        follow_up_result = MaintenanceDispatchResult(
            status="error",
            queue_name=None,
            enqueued_count=0,
            due_job_count=0,
            next_scheduled_delay_seconds=None,
            rq_job_ids=[],
            message=f"维护任务 follow-up 派发失败: {exc}",
        )
    payload = {
        "execution": execution_result.as_dict(),
        "follow_up": follow_up_result.as_dict(),
    }
    logger.info(
        "RQ maintenance dispatch 完成: processed=%s succeeded=%s failed=%s skipped=%s retried=%s follow_up_status=%s",
        execution_result.processed_count,
        execution_result.succeeded_count,
        execution_result.failed_count,
        execution_result.skipped_count,
        execution_result.retried_count,
        follow_up_result.status,
    )
    return payload


def enqueue_trajectory_refresh_job(
    *,
    experiment_id: str,
    batch_id: str,
    simulation_time_ms_values: list[int],
    agent_ids: list[str],
) -> TrajectoryRefreshDispatchResult:
    """把一次轨迹聚合/采样刷新投递到共享 RQ 队列。

    轨迹上报接口已经完成原始数据落库；这里投递失败不会影响上报成功，只会让聚合刷新延后。
    """

    unique_time_values = sorted({int(value) for value in simulation_time_ms_values if int(value) >= 0})
    unique_agent_ids = sorted({str(agent_id) for agent_id in agent_ids if str(agent_id)})
    if not unique_time_values or not unique_agent_ids:
        return TrajectoryRefreshDispatchResult(
            status="skipped",
            queue_name=None,
            rq_job_id=None,
            message="没有需要刷新的轨迹时间点或 agent 列表",
        )

    queue = _get_maintenance_queue(required=False)
    if queue is None:
        return TrajectoryRefreshDispatchResult(
            status="disabled",
            queue_name=None,
            rq_job_id=None,
            message="Redis 未启用，跳过轨迹刷新 RQ 任务投递",
        )

    settings = get_settings()
    try:
        rq_job = queue.enqueue(
            "backend.app.infrastructure.workers.rq_worker.process_trajectory_refresh_job",
            experiment_id=experiment_id,
            simulation_time_ms_values=unique_time_values,
            agent_ids=unique_agent_ids,
            job_timeout=settings.maintenance_rq_job_timeout_seconds,
            ttl=settings.maintenance_rq_ttl_seconds,
            result_ttl=settings.maintenance_rq_result_ttl_seconds,
            failure_ttl=settings.maintenance_rq_failure_ttl_seconds,
            description=f"trajectory-refresh:{experiment_id}:{batch_id}",
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "RQ 轨迹刷新任务投递失败: queue=%s experiment_id=%s batch_id=%s error=%s",
            getattr(queue, "name", None),
            experiment_id,
            batch_id,
            exc,
        )
        return TrajectoryRefreshDispatchResult(
            status="error",
            queue_name=str(getattr(queue, "name", "")) or None,
            rq_job_id=None,
            message=f"RQ 轨迹刷新任务投递失败: {exc}",
        )

    logger.info(
        "RQ 轨迹刷新任务已投递: queue=%s experiment_id=%s batch_id=%s rq_job_id=%s",
        queue.name,
        experiment_id,
        batch_id,
        rq_job.id,
    )
    return TrajectoryRefreshDispatchResult(
        status="enqueued",
        queue_name=str(queue.name),
        rq_job_id=str(rq_job.id),
        message="轨迹刷新任务已投递到 RQ",
    )


def process_trajectory_refresh_job(
    *,
    experiment_id: str,
    simulation_time_ms_values: list[int],
    agent_ids: list[str],
) -> dict[str, Any]:
    """RQ worker 执行入口：刷新轨迹聚合和秒级采样轨迹。"""

    logger.info(
        "RQ 轨迹刷新任务开始: experiment_id=%s simulation_time_count=%s agent_count=%s",
        experiment_id,
        len(simulation_time_ms_values),
        len(agent_ids),
    )
    session = get_session_factory()()
    try:
        from backend.app.application.trajectories.service import refresh_trajectory_views

        result = refresh_trajectory_views(
            session,
            experiment_id=experiment_id,
            simulation_time_ms_values=simulation_time_ms_values,
            agent_ids=agent_ids,
        )
    finally:
        session.close()

    payload = result.as_dict()
    logger.info(
        "RQ 轨迹刷新任务完成: experiment_id=%s trajectory_buckets=%s agent_buckets=%s sampled_agent_count=%s",
        experiment_id,
        payload["refreshed_trajectory_buckets"],
        payload["refreshed_agent_buckets"],
        payload["sampled_agent_count"],
    )
    return payload


def run_maintenance_rq_worker(
    *,
    burst: bool = False,
    max_jobs: int | None = None,
    with_scheduler: bool = True,
    worker_name: str | None = None,
) -> dict[str, Any]:
    """启动共享 maintenance 队列的 RQ worker。

    项目统一使用 SimpleWorker，任务在当前进程内执行，避免 Windows 下 fork worker 不可用。
    with_scheduler 控制 RQ 自身的延迟任务调度；如果在 Windows 遇到 fork 报错，可通过命令关闭 scheduler。
    """

    settings = get_settings()
    db_health = check_database_health()
    if db_health.get("status") != "ok":
        logger.error(
            "RQ worker 启动失败，数据库不可用: database=%s message=%s",
            db_health.get("database"),
            db_health.get("message"),
        )
        raise AppError(
            message="RQ worker 启动失败，数据库不可用",
            code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
            status_code=503,
            data=db_health,
        )

    redis_health = get_redis_client().healthcheck()
    if redis_health.get("status") != "ok":
        logger.error("RQ worker 启动失败，Redis 不可用: message=%s", redis_health.get("message"))
        raise AppError(
            message="RQ worker 启动失败，Redis 不可用",
            code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
            status_code=503,
            data=redis_health,
        )

    queue = _get_maintenance_queue(required=True)
    _, simple_worker_class = _import_rq_components()
    worker = simple_worker_class(
        [queue],
        connection=queue.connection,
        name=worker_name,
    )
    logger.info(
        "RQ worker 启动: queue=%s worker_name=%s burst=%s max_jobs=%s with_scheduler=%s",
        queue.name,
        worker.name,
        burst,
        max_jobs,
        with_scheduler,
    )
    work_result = worker.work(
        burst=burst,
        max_jobs=max_jobs,
        with_scheduler=with_scheduler,
        logging_level=settings.log_level.upper(),
    )
    logger.info(
        "RQ worker 已停止: queue=%s worker_name=%s work_result=%s",
        queue.name,
        worker.name,
        work_result,
    )
    return {
        "backend": "rq",
        "queue_name": queue.name,
        "worker_name": worker.name,
        "burst": burst,
        "max_jobs": max_jobs,
        "with_scheduler": with_scheduler,
        "work_result": bool(work_result),
    }


def _enqueue_dispatch_jobs(
    *,
    queue: Any,
    fanout: int,
    delay_seconds: float | None,
) -> MaintenanceDispatchResult:
    """向 RQ 写入 maintenance dispatch job。

    入队的是 dispatch 函数，不是具体某一条维护任务 ID；具体任务由数据库 pending 查询决定。
    """

    settings = get_settings()
    rq_job_ids: list[str] = []
    try:
        for _ in range(max(fanout, 1)):
            if delay_seconds is not None and delay_seconds > 0:
                rq_job = queue.enqueue_in(
                    timedelta(seconds=delay_seconds),
                    "backend.app.infrastructure.workers.rq_worker.process_maintenance_dispatch",
                    limit=1,
                    job_timeout=settings.maintenance_rq_job_timeout_seconds,
                    ttl=settings.maintenance_rq_ttl_seconds,
                    result_ttl=settings.maintenance_rq_result_ttl_seconds,
                    failure_ttl=settings.maintenance_rq_failure_ttl_seconds,
                    description="maintenance-dispatch-delayed",
                )
            else:
                rq_job = queue.enqueue(
                    "backend.app.infrastructure.workers.rq_worker.process_maintenance_dispatch",
                    limit=1,
                    job_timeout=settings.maintenance_rq_job_timeout_seconds,
                    ttl=settings.maintenance_rq_ttl_seconds,
                    result_ttl=settings.maintenance_rq_result_ttl_seconds,
                    failure_ttl=settings.maintenance_rq_failure_ttl_seconds,
                    description="maintenance-dispatch",
                )
            rq_job_ids.append(str(rq_job.id))
    except Exception as exc:  # noqa: BLE001
        logger.warning("RQ maintenance dispatch 入队失败: queue=%s error=%s", getattr(queue, "name", None), exc)
        return MaintenanceDispatchResult(
            status="error",
            queue_name=str(getattr(queue, "name", "")) or None,
            enqueued_count=0,
            due_job_count=0,
            next_scheduled_delay_seconds=delay_seconds,
            rq_job_ids=[],
            message=f"RQ 入队失败: {exc}",
        )

    return MaintenanceDispatchResult(
        status="enqueued" if delay_seconds in (None, 0) else "scheduled",
        queue_name=str(queue.name),
        enqueued_count=len(rq_job_ids),
        due_job_count=0,
        next_scheduled_delay_seconds=delay_seconds,
        rq_job_ids=rq_job_ids,
        message="RQ 入队成功",
    )


def _calculate_delay_seconds(scheduled_at: datetime | None) -> float | None:
    """把数据库 scheduled_at 转成 RQ enqueue_in 需要的延迟秒数。"""

    if scheduled_at is None:
        return None
    now = datetime.now(timezone.utc)
    return max((scheduled_at - now).total_seconds(), 0.0)


def _get_maintenance_queue(*, required: bool) -> Any | None:
    """获取维护和轨迹刷新共用的 RQ queue。"""

    settings = get_settings()
    client = get_redis_client()
    connection_getter = getattr(client, "get_rq_connection", None)
    if connection_getter is None:
        connection = client.get_connection()
    else:
        connection = connection_getter()
    if connection is None:
        if required:
            raise AppError(
                message="Redis 不可用，无法启动 RQ worker",
                code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
                status_code=503,
                data={"backend": "redis", "enabled": client.enabled},
            )
        logger.info("Redis 未启用，跳过 RQ queue 获取")
        return None

    queue_class, _ = _import_rq_components()
    return queue_class(settings.maintenance_queue_name, connection=connection)


def _build_rq_job_request_id(job: Any) -> str:
    """为 RQ job 构造日志 request_id。"""

    job_id = getattr(job, "id", None)
    if job_id:
        return f"rq-job-{job_id}"
    return f"rq-job-{uuid4().hex[:8]}"


def _extract_rq_job_experiment_id(job: Any) -> str | None:
    """从 RQ job kwargs 中提取 experiment_id，用于日志上下文。"""

    kwargs = getattr(job, "kwargs", None)
    if isinstance(kwargs, dict):
        experiment_id = kwargs.get("experiment_id")
        if experiment_id is not None and str(experiment_id) != "":
            return str(experiment_id)
    return None


@lru_cache(maxsize=1)
def _import_rq_components() -> tuple[Any, Any]:
    """延迟导入 RQ，并包装 SimpleWorker 注入请求上下文。

    延迟导入可以让不启用 Redis/RQ 的普通后端启动路径不受 RQ 依赖和 Windows 补丁影响。
    """

    _patch_rq_windows_compat()
    from rq import Queue, SimpleWorker

    class ContextAwareSimpleWorker(SimpleWorker):
        def execute_job(self, job: Any, queue: Any):
            tokens = set_request_context(
                request_id=_build_rq_job_request_id(job),
                experiment_id=_extract_rq_job_experiment_id(job),
            )
            try:
                return super().execute_job(job, queue)
            finally:
                reset_request_context(tokens)

    return Queue, ContextAwareSimpleWorker


def _patch_rq_windows_compat() -> None:
    """在 Windows 上把 RQ 内部请求 fork context 的行为改成 spawn。

    RQ 的部分模块会直接调用 multiprocessing.get_context("fork")。Windows 没有 fork，
    因此需要在导入 rq.Queue/SimpleWorker 前打补丁，避免启动 worker 或 scheduler 时报错。
    """

    if getattr(multiprocessing, "_crowd_sim_rq_windows_compat", False):
        return

    original_get_context = multiprocessing.get_context
    try:
        original_get_context("fork")
    except ValueError:

        def _patched_get_context(method: str | None = None):  # type: ignore[override]
            if method == "fork":
                return original_get_context("spawn")
            return original_get_context(method)

        multiprocessing.get_context = _patched_get_context  # type: ignore[assignment]
        logger.info("已启用 RQ Windows 兼容补丁: fork -> spawn")

    multiprocessing._crowd_sim_rq_windows_compat = True  # type: ignore[attr-defined]
