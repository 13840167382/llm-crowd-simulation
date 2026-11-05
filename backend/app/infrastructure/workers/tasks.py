"""本地直接执行型后台维护任务入口。

这里不依赖 RQ，而是通过轮询数据库中的 experiment_maintenance_jobs 表直接执行维护任务。
适合本地开发、没有 Redis 的环境，或临时手动补跑任务；生产环境如果启用 RQ，通常会走
infrastructure.workers.rq_worker。
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from time import perf_counter
from uuid import uuid4

from backend.app.application.maintenance.service import (
    MaintenanceJobExecutionResult,
    run_pending_maintenance_jobs,
)
from backend.app.core.config import get_settings
from backend.app.api import error_codes
from backend.app.api.errors import AppError
from backend.app.infrastructure.db.session import check_database_health
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.infrastructure.db.session import get_session_factory

logger = get_logger(__name__)


@dataclass(slots=True)
class MaintenanceWorkerLoopResult:
    """本地直接执行型维护 worker 的整轮执行汇总。"""

    worker_id: str
    poll_count: int
    active_poll_count: int
    idle_poll_count: int
    processed_count: int
    succeeded_count: int
    failed_count: int
    skipped_count: int
    retried_count: int
    last_job_ids: list[int]
    uptime_ms: int
    stop_reason: str

    def as_dict(self) -> dict[str, object]:
        """转换为适合日志与命令行输出的字典。"""

        return asdict(self)


def list_registered_tasks() -> list[str]:
    """返回当前已经注册的后台任务类型。"""

    return [
        "analytics_refresh",
        "heatmap_prewarm",
        "runtime_data_cleanup",
        "experiment_archive",
    ]


def run_pending_tasks_result(*, limit: int = 20) -> MaintenanceJobExecutionResult:
    """执行一批待处理维护任务，并保留结构化结果对象。"""

    session = get_session_factory()()
    try:
        return run_pending_maintenance_jobs(session, limit=limit)
    finally:
        session.close()


def run_pending_tasks(*, limit: int = 20) -> dict[str, object]:
    """执行一批待处理的后台维护任务。"""

    return run_pending_tasks_result(limit=limit).as_dict()


async def run_maintenance_worker(
    *,
    limit: int = 20,
    poll_interval_seconds: float = 5.0,
    stop_event: asyncio.Event | None = None,
    max_cycles: int | None = None,
    stop_when_idle: bool = False,
) -> MaintenanceWorkerLoopResult:
    """以本地轮询方式持续消费维护任务。

    每轮最多处理 limit 个任务；空闲时等待 poll_interval_seconds，避免忙等。
    max_cycles 和 stop_when_idle 主要给命令行测试或一次性补偿任务使用。
    """

    settings = get_settings()
    effective_stop_event = stop_event or asyncio.Event()
    worker_id = uuid4().hex[:8]
    started_at = perf_counter()
    poll_count = 0
    active_poll_count = 0
    idle_poll_count = 0
    consecutive_idle_polls = 0
    processed_count = 0
    succeeded_count = 0
    failed_count = 0
    skipped_count = 0
    retried_count = 0
    last_job_ids: list[int] = []
    stop_reason = "unknown"

    # worker 启动前先检查数据库，避免进入循环后不断失败刷日志。
    startup_health = await asyncio.to_thread(check_database_health)
    if startup_health.get("status") != "ok":
        logger.error(
            "后台维护 worker 启动预检失败: worker_id=%s database=%s message=%s",
            worker_id,
            startup_health.get("database"),
            startup_health.get("message"),
        )
        raise AppError(
            message="后台维护 worker 启动预检失败，数据库不可用",
            code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
            status_code=503,
            data=startup_health,
        )

    logger.info(
        "后台维护 worker 启动预检通过: worker_id=%s database=%s server_version=%s",
        worker_id,
        startup_health.get("database"),
        startup_health.get("server_version"),
    )

    logger.info(
        "后台维护 worker 启动: worker_id=%s limit=%s poll_interval_seconds=%s idle_log_every_polls=%s max_cycles=%s stop_when_idle=%s",
        worker_id,
        limit,
        poll_interval_seconds,
        settings.maintenance_worker_idle_log_every_polls,
        max_cycles,
        stop_when_idle,
    )

    while True:
        poll_count += 1
        poll_started_at = perf_counter()
        logger.info(
            "后台维护 worker 开始轮询: worker_id=%s poll=%s limit=%s cumulative_processed=%s",
            worker_id,
            poll_count,
            limit,
            processed_count,
        )
        # 维护任务包含同步数据库操作，用 to_thread 避免阻塞当前 asyncio 事件循环。
        batch_result = await asyncio.to_thread(run_pending_tasks_result, limit=limit)
        poll_duration_ms = int((perf_counter() - poll_started_at) * 1000)
        processed_count += batch_result.processed_count
        succeeded_count += batch_result.succeeded_count
        failed_count += batch_result.failed_count
        skipped_count += batch_result.skipped_count
        retried_count += batch_result.retried_count
        if batch_result.job_ids:
            last_job_ids = batch_result.job_ids

        if batch_result.processed_count > 0:
            active_poll_count += 1
            consecutive_idle_polls = 0
            throughput = _calculate_jobs_per_second(
                processed_count=batch_result.processed_count,
                duration_ms=poll_duration_ms,
            )
            logger.info(
                "后台维护 worker 完成活跃轮询: worker_id=%s poll=%s duration_ms=%s processed=%s succeeded=%s failed=%s skipped=%s retried=%s throughput_jobs_per_sec=%s cumulative_processed=%s job_ids=%s",
                worker_id,
                poll_count,
                poll_duration_ms,
                batch_result.processed_count,
                batch_result.succeeded_count,
                batch_result.failed_count,
                batch_result.skipped_count,
                batch_result.retried_count,
                throughput,
                processed_count,
                batch_result.job_ids,
            )
        else:
            idle_poll_count += 1
            consecutive_idle_polls += 1
            uptime_ms = int((perf_counter() - started_at) * 1000)
            if stop_when_idle:
                logger.info(
                    "后台维护 worker 命中空闲退出条件: worker_id=%s poll=%s idle_poll_count=%s uptime_ms=%s",
                    worker_id,
                    poll_count,
                    idle_poll_count,
                    uptime_ms,
                )
            elif (
                consecutive_idle_polls == 1
                or consecutive_idle_polls % max(settings.maintenance_worker_idle_log_every_polls, 1) == 0
            ):
                logger.info(
                    "后台维护 worker 空闲心跳: worker_id=%s poll=%s consecutive_idle_polls=%s idle_poll_count=%s next_sleep_seconds=%s uptime_ms=%s cumulative_processed=%s",
                    worker_id,
                    poll_count,
                    consecutive_idle_polls,
                    idle_poll_count,
                    poll_interval_seconds,
                    uptime_ms,
                    processed_count,
                )
            else:
                logger.debug(
                    "后台维护 worker 空闲轮询: worker_id=%s poll=%s consecutive_idle_polls=%s duration_ms=%s",
                    worker_id,
                    poll_count,
                    consecutive_idle_polls,
                    poll_duration_ms,
                )

        if effective_stop_event.is_set():
            stop_reason = "signal"
            break

        if max_cycles is not None and poll_count >= max_cycles:
            stop_reason = "max_cycles"
            break

        if batch_result.processed_count == 0:
            # 只有空轮询才 sleep；刚处理过任务时立即进入下一轮，尽快清空积压任务。
            if stop_when_idle:
                stop_reason = "idle"
                break
            should_stop = await _wait_for_stop(
                stop_event=effective_stop_event,
                timeout_seconds=poll_interval_seconds,
            )
            if should_stop:
                stop_reason = "signal"
                break
            continue

    logger.info(
        "后台维护 worker 停止: worker_id=%s poll_count=%s active_polls=%s idle_polls=%s processed=%s succeeded=%s failed=%s skipped=%s uptime_ms=%s stop_reason=%s last_job_ids=%s",
        worker_id,
        poll_count,
        active_poll_count,
        idle_poll_count,
        processed_count,
        succeeded_count,
        failed_count,
        skipped_count,
        int((perf_counter() - started_at) * 1000),
        stop_reason,
        last_job_ids,
    )
    logger.info(
        "后台维护 worker 最终汇总: worker_id=%s processed=%s succeeded=%s failed=%s skipped=%s retried=%s uptime_ms=%s",
        worker_id,
        processed_count,
        succeeded_count,
        failed_count,
        skipped_count,
        retried_count,
        int((perf_counter() - started_at) * 1000),
    )
    return MaintenanceWorkerLoopResult(
        worker_id=worker_id,
        poll_count=poll_count,
        active_poll_count=active_poll_count,
        idle_poll_count=idle_poll_count,
        processed_count=processed_count,
        succeeded_count=succeeded_count,
        failed_count=failed_count,
        skipped_count=skipped_count,
        retried_count=retried_count,
        last_job_ids=last_job_ids,
        uptime_ms=int((perf_counter() - started_at) * 1000),
        stop_reason=stop_reason,
    )


async def _wait_for_stop(*, stop_event: asyncio.Event, timeout_seconds: float) -> bool:
    """在轮询间隔内等待停止信号，避免空轮询时忙等。"""

    if timeout_seconds <= 0:
        await asyncio.sleep(0)
        return stop_event.is_set()

    try:
        await asyncio.wait_for(stop_event.wait(), timeout=timeout_seconds)
        return True
    except asyncio.TimeoutError:
        return False


def _calculate_jobs_per_second(*, processed_count: int, duration_ms: int) -> float:
    """把单轮任务吞吐换算成每秒处理量，便于日志排障。"""

    if processed_count <= 0 or duration_ms <= 0:
        return 0.0
    return round(processed_count / (duration_ms / 1000), 3)
