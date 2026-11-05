"""后端命令行入口。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import uvicorn

from backend.app.application.benchmarking import run_formal_benchmark, run_p0_benchmark_suite
from backend.app.application.benchmarking.p0_suite import (
    DEFAULT_CAPACITY_BATCH_SIZES,
    DEFAULT_CONCURRENCY_LEVELS,
    DEFAULT_DECISION_INTERVAL_SECONDS,
    DEFAULT_DECISION_PERIODIC_BATCHES,
    DEFAULT_FULL_FLOW_RUNS,
    DEFAULT_HTTP_TIMEOUT_SECONDS,
    DEFAULT_MIXED_CONCURRENCY,
    parse_positive_int_tuple,
)
from backend.app.application.maintenance.service import (
    cleanup_experiment_runtime_data,
    query_maintenance_jobs,
    replay_maintenance_jobs_from,
    reset_failed_maintenance_jobs,
)
from backend.app.api.errors import AppError
from backend.app.core.config import get_settings
from backend.app.domain.enums import MaintenanceJobStatus
from backend.app.infrastructure.cache import close_redis_client
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.observability.logger import configure_logging, get_logger
from backend.app.infrastructure.workers.rq_worker import (
    dispatch_pending_maintenance_jobs,
    run_maintenance_rq_worker,
)
from backend.app.infrastructure.workers.tasks import run_pending_tasks

logger = get_logger(__name__)


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    args = _parse_args(sys.argv[1:])

    try:
        if args.command == "cleanup-runtime-data":
            _run_cleanup_runtime_data(
                experiment_id=args.experiment_id,
                keep_heatmap_cache=args.keep_heatmap_cache,
            )
            return
        if args.command == "run-maintenance-jobs":
            _run_maintenance_jobs(limit=args.limit)
            return
        if args.command == "dispatch-maintenance-jobs":
            _dispatch_maintenance_jobs(limit=args.limit, fanout=args.fanout)
            return
        if args.command == "show-maintenance-jobs":
            _show_maintenance_jobs(
                experiment_id=args.experiment_id,
                status=args.status,
                limit=args.limit,
                include_payload=args.include_payload,
                include_result=args.include_result,
            )
            return
        if args.command == "reset-maintenance-jobs":
            _reset_maintenance_jobs(
                experiment_id=args.experiment_id,
                job_id=args.job_id,
                dispatch=not args.without_dispatch,
            )
            return
        if args.command == "replay-maintenance-jobs":
            _replay_maintenance_jobs(
                experiment_id=args.experiment_id,
                from_job_id=args.from_job_id,
                dispatch=not args.without_dispatch,
            )
            return
        if args.command == "run-formal-benchmark":
            _run_formal_benchmark(
                report_path=args.report_path,
                output_json_path=args.output_json_path,
            )
            return
        if args.command == "run-p0-benchmark-suite":
            _run_p0_benchmark_suite(
                base_url=args.base_url,
                report_path=args.report_path,
                output_json_path=args.output_json_path,
                concurrency_levels=args.concurrency_levels,
                mixed_concurrency=args.mixed_concurrency,
                full_flow_runs=args.full_flow_runs,
                capacity_batch_sizes=args.capacity_batch_sizes,
                decision_interval_seconds=args.decision_interval_seconds,
                decision_periodic_batches=args.decision_periodic_batches,
                http_timeout_seconds=args.http_timeout_seconds,
            )
            return
        if args.command == "run-maintenance-worker":
            _run_maintenance_worker(
                burst=args.burst,
                max_jobs=args.max_jobs,
                with_scheduler=not args.without_scheduler,
                worker_name=args.worker_name,
            )
            return

        _run_server()
    finally:
        close_redis_client()


def _run_server() -> None:
    settings = get_settings()
    reload_excludes = None
    if settings.reload:
        reload_excludes = _build_reload_excludes(settings.log_dir)

    uvicorn.run(
        "backend.app.main:create_app",
        factory=True,
        host=settings.host,
        port=settings.port,
        reload=settings.reload,
        reload_excludes=reload_excludes,
        log_level=settings.log_level,
    )


def _build_reload_excludes(log_dir: str) -> list[str]:
    """构建热更新排除项，避免日志文件写入触发 watchfiles 噪音。"""

    backend_root = Path(__file__).resolve().parent
    current_working_directory = Path.cwd().resolve()
    resolved_log_dir = Path(log_dir)
    if not resolved_log_dir.is_absolute():
        resolved_log_dir = backend_root / resolved_log_dir

    patterns = {"*.log", "logs", "logs/*", "backend/logs", "backend/logs/*"}

    try:
        relative_log_dir = resolved_log_dir.relative_to(current_working_directory)
    except ValueError:
        relative_log_dir = None

    if relative_log_dir is not None:
        normalized = relative_log_dir.as_posix()
        if normalized and normalized != ".":
            patterns.add(normalized)
            patterns.add(f"{normalized}/*")

    return sorted(patterns)


def _run_cleanup_runtime_data(*, experiment_id: str, keep_heatmap_cache: bool) -> None:
    session = get_session_factory()()
    try:
        result = cleanup_experiment_runtime_data(
            session,
            experiment_id=experiment_id,
            purge_heatmap_cache=not keep_heatmap_cache,
        )
    finally:
        session.close()

    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def _run_maintenance_jobs(*, limit: int) -> None:
    result = run_pending_tasks(limit=limit)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _dispatch_maintenance_jobs(*, limit: int, fanout: int) -> None:
    logger.info(
        "准备派发数据库中的待执行维护任务到 RQ: limit=%s fanout=%s",
        limit,
        fanout,
    )
    result = dispatch_pending_maintenance_jobs(limit=limit, fanout=fanout)
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def _show_maintenance_jobs(
    *,
    experiment_id: str,
    status: str | None,
    limit: int,
    include_payload: bool,
    include_result: bool,
) -> None:
    session = get_session_factory()()
    try:
        result = query_maintenance_jobs(
            session,
            experiment_id=experiment_id,
            status=MaintenanceJobStatus(status) if status is not None else None,
            limit=limit,
            include_payload=include_payload,
            include_result=include_result,
        )
    finally:
        session.close()
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def _reset_maintenance_jobs(*, experiment_id: str, job_id: int | None, dispatch: bool) -> None:
    session = get_session_factory()()
    try:
        result = reset_failed_maintenance_jobs(
            session,
            experiment_id=experiment_id,
            job_id=job_id,
            dispatch=dispatch,
        )
    finally:
        session.close()
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def _replay_maintenance_jobs(*, experiment_id: str, from_job_id: int, dispatch: bool) -> None:
    session = get_session_factory()()
    try:
        result = replay_maintenance_jobs_from(
            session,
            experiment_id=experiment_id,
            from_job_id=from_job_id,
            dispatch=dispatch,
        )
    finally:
        session.close()
    print(json.dumps(result.as_dict(), ensure_ascii=False, indent=2))


def _run_formal_benchmark(*, report_path: str | None, output_json_path: str | None) -> None:
    result = run_formal_benchmark(report_path=report_path, output_json_path=output_json_path)
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_p0_benchmark_suite(
    *,
    base_url: str | None,
    report_path: str | None,
    output_json_path: str | None,
    concurrency_levels: str | None,
    mixed_concurrency: int,
    full_flow_runs: int,
    capacity_batch_sizes: str | None,
    decision_interval_seconds: float,
    decision_periodic_batches: int,
    http_timeout_seconds: float,
) -> None:
    result = run_p0_benchmark_suite(
        base_url=base_url,
        report_path=report_path,
        output_json_path=output_json_path,
        concurrency_levels=parse_positive_int_tuple(
            concurrency_levels,
            fallback=DEFAULT_CONCURRENCY_LEVELS,
        ),
        mixed_concurrency=mixed_concurrency,
        full_flow_runs=full_flow_runs,
        capacity_batch_sizes=parse_positive_int_tuple(
            capacity_batch_sizes,
            fallback=DEFAULT_CAPACITY_BATCH_SIZES,
        ),
        decision_interval_seconds=decision_interval_seconds,
        decision_periodic_batches=decision_periodic_batches,
        http_timeout_seconds=http_timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _run_maintenance_worker(
    *,
    burst: bool,
    max_jobs: int | None,
    with_scheduler: bool,
    worker_name: str | None,
) -> None:
    logger.info(
        "准备启动 RQ maintenance worker: burst=%s max_jobs=%s with_scheduler=%s worker_name=%s",
        burst,
        max_jobs,
        with_scheduler,
        worker_name,
    )
    try:
        result = run_maintenance_rq_worker(
            burst=burst,
            max_jobs=max_jobs,
            with_scheduler=with_scheduler,
            worker_name=worker_name,
        )
    except AppError as exc:
        logger.error(
            "RQ maintenance worker 启动失败: code=%s status=%s message=%s data=%s",
            exc.code,
            exc.status_code,
            exc.message,
            exc.data,
        )
        print(
            json.dumps(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "data": exc.data,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        raise SystemExit(2) from exc
    print(json.dumps(result, ensure_ascii=False, indent=2))


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="人群流动模拟系统后端命令行入口")
    subparsers = parser.add_subparsers(dest="command")

    cleanup_parser = subparsers.add_parser(
        "cleanup-runtime-data",
        help="执行实验级原始运行数据清理",
    )
    cleanup_parser.add_argument(
        "--experiment-id",
        required=True,
        help="要清理的实验对外标识，例如 exp_xxx",
    )
    cleanup_parser.add_argument(
        "--keep-heatmap-cache",
        action="store_true",
        help="清理时保留数据库里的热力图缓存记录，只删除决策与轨迹原始表数据",
    )

    maintenance_parser = subparsers.add_parser(
        "run-maintenance-jobs",
        help="执行当前数据库中待处理的实验维护任务",
    )
    maintenance_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="本次最多处理的任务数量，默认 20",
    )

    dispatch_parser = subparsers.add_parser(
        "dispatch-maintenance-jobs",
        help="把数据库中待执行的维护任务补派发到 RQ 队列",
    )
    dispatch_parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="本次最多检查多少条待执行任务，默认 20",
    )
    dispatch_parser.add_argument(
        "--fanout",
        type=int,
        default=1,
        help="本次最多创建多少个 RQ 调度 job，默认 1",
    )

    show_jobs_parser = subparsers.add_parser(
        "show-maintenance-jobs",
        help="查询某个实验的维护任务状态",
    )
    show_jobs_parser.add_argument(
        "--experiment-id",
        required=True,
        help="要查询的实验对外标识，例如 exp_xxx",
    )
    show_jobs_parser.add_argument(
        "--status",
        choices=[status.value for status in MaintenanceJobStatus],
        default=None,
        help="只查看指定状态的任务，可选 pending/running/succeeded/failed",
    )
    show_jobs_parser.add_argument(
        "--limit",
        type=int,
        default=50,
        help="最多返回多少条任务记录，默认 50",
    )
    show_jobs_parser.add_argument(
        "--include-payload",
        action="store_true",
        help="结果中附带任务 payload_json",
    )
    show_jobs_parser.add_argument(
        "--include-result",
        action="store_true",
        help="结果中附带任务 result_json",
    )

    reset_jobs_parser = subparsers.add_parser(
        "reset-maintenance-jobs",
        help="把失败维护任务重置为待执行，并自动补派发到 RQ",
    )
    reset_jobs_parser.add_argument(
        "--experiment-id",
        required=True,
        help="要处理的实验对外标识，例如 exp_xxx",
    )
    reset_jobs_parser.add_argument(
        "--job-id",
        type=int,
        default=None,
        help="只重置指定失败任务；不传时会重置该实验下全部失败任务",
    )
    reset_jobs_parser.add_argument(
        "--without-dispatch",
        action="store_true",
        help="只重置数据库状态，不自动补派发到 RQ",
    )

    replay_jobs_parser = subparsers.add_parser(
        "replay-maintenance-jobs",
        help="从指定维护任务开始重放后续维护链路，并自动补派发到 RQ",
    )
    replay_jobs_parser.add_argument(
        "--experiment-id",
        required=True,
        help="要处理的实验对外标识，例如 exp_xxx",
    )
    replay_jobs_parser.add_argument(
        "--from-job-id",
        required=True,
        type=int,
        help="从哪条维护任务开始重放，当前任务及其后续任务都会被重置",
    )
    replay_jobs_parser.add_argument(
        "--without-dispatch",
        action="store_true",
        help="只重置数据库状态，不自动补派发到 RQ",
    )

    benchmark_parser = subparsers.add_parser(
        "run-formal-benchmark",
        help="运行正式压测，自动造数并输出 Markdown 报告与 JSON 结果",
    )
    benchmark_parser.add_argument(
        "--report-path",
        default=None,
        help="Markdown 报告输出路径，默认写入 backend/正式压测报告.md",
    )
    benchmark_parser.add_argument(
        "--output-json-path",
        default=None,
        help="原始 JSON 结果输出路径，默认写入 backend/logs/benchmarks/formal-benchmark-latest.json",
    )

    p0_benchmark_parser = subparsers.add_parser(
        "run-p0-benchmark-suite",
        help="运行 P0 压测套件，覆盖真实 HTTP、并发、混合流量、闭环、维护链路和容量曲线",
    )
    p0_benchmark_parser.add_argument(
        "--base-url",
        default=None,
        help="真实 HTTP 服务基地址，默认根据当前 APP_HOST / APP_PORT 推导",
    )
    p0_benchmark_parser.add_argument(
        "--report-path",
        default=None,
        help="Markdown 报告输出路径，默认写入 backend/P0压测报告.md",
    )
    p0_benchmark_parser.add_argument(
        "--output-json-path",
        default=None,
        help="JSON 结果输出路径，默认写入 backend/logs/benchmarks/p0-benchmark-latest.json",
    )
    p0_benchmark_parser.add_argument(
        "--concurrency-levels",
        default=",".join(str(value) for value in DEFAULT_CONCURRENCY_LEVELS),
        help="并发阶梯档位，逗号分隔，例如 1,10,20",
    )
    p0_benchmark_parser.add_argument(
        "--mixed-concurrency",
        type=int,
        default=DEFAULT_MIXED_CONCURRENCY,
        help=f"混合流量总并发，默认 {DEFAULT_MIXED_CONCURRENCY}",
    )
    p0_benchmark_parser.add_argument(
        "--full-flow-runs",
        type=int,
        default=DEFAULT_FULL_FLOW_RUNS,
        help=f"完整闭环与维护链路的重复测量次数，默认 {DEFAULT_FULL_FLOW_RUNS}",
    )
    p0_benchmark_parser.add_argument(
        "--capacity-batch-sizes",
        default=",".join(str(value) for value in DEFAULT_CAPACITY_BATCH_SIZES),
        help="轨迹容量曲线批次大小，逗号分隔，例如 500,1000,2000,5000,10000",
    )
    p0_benchmark_parser.add_argument(
        "--decision-interval-seconds",
        type=float,
        default=DEFAULT_DECISION_INTERVAL_SECONDS,
        help=f"周期性 LLM 决策批次的真实发送间隔，单位秒，默认 {DEFAULT_DECISION_INTERVAL_SECONDS}",
    )
    p0_benchmark_parser.add_argument(
        "--decision-periodic-batches",
        type=int,
        default=DEFAULT_DECISION_PERIODIC_BATCHES,
        help=f"周期性 LLM 决策批次总数，默认 {DEFAULT_DECISION_PERIODIC_BATCHES}",
    )
    p0_benchmark_parser.add_argument(
        "--http-timeout-seconds",
        type=float,
        default=DEFAULT_HTTP_TIMEOUT_SECONDS,
        help=f"真实 HTTP 请求超时时间，单位秒，默认 {DEFAULT_HTTP_TIMEOUT_SECONDS}",
    )

    maintenance_worker_parser = subparsers.add_parser(
        "run-maintenance-worker",
        help="以 RQ worker 模式持续消费实验维护任务",
    )
    maintenance_worker_parser.add_argument(
        "--burst",
        action="store_true",
        help="只处理当前队列里已有的任务后退出，适合调试或临时执行",
    )
    maintenance_worker_parser.add_argument(
        "--max-jobs",
        type=int,
        default=None,
        help="最多处理多少个 RQ job 后退出，默认不限制",
    )
    maintenance_worker_parser.add_argument(
        "--without-scheduler",
        action="store_true",
        help="关闭 RQ scheduler 支持，仅适用于不需要延迟重试的调试场景",
    )
    maintenance_worker_parser.add_argument(
        "--worker-name",
        default=None,
        help="自定义 worker 名称，默认由 RQ 自动生成",
    )

    return parser.parse_args(argv)


if __name__ == "__main__":
    main()
