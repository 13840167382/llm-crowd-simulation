"""P0 压测套件。"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
import json
import logging
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import urlparse
from uuid import uuid4

from sqlalchemy import delete, select

from backend.app.application.benchmarking import service as formal_service
from backend.app.core.config import get_settings
from backend.app.domain.enums import CompletionReason, MaintenanceJobStatus
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    CrowdTypeTemplate,
    DecisionBatch,
    DecisionMetric1m,
    Experiment,
    ExperimentAgent,
    ExperimentAllocation,
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
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.infrastructure.workers.rq_worker import run_maintenance_rq_worker
from backend.app.infrastructure.workers.tasks import run_maintenance_worker

logger = get_logger(__name__)

DEFAULT_CONCURRENCY_LEVELS = (1, 10, 20)
DEFAULT_MIXED_CONCURRENCY = 12
DEFAULT_FULL_FLOW_RUNS = 3
DEFAULT_CAPACITY_BATCH_SIZES = (500, 1000, 2000, 5000, 10000)
DEFAULT_DECISION_INTERVAL_SECONDS = 5.0
DEFAULT_DECISION_PERIODIC_BATCHES = 6
DEFAULT_HTTP_TIMEOUT_SECONDS = 120.0


class RealHttpBenchmarkClient:
    """面向真实 HTTP 服务的压测客户端。"""

    def __init__(self, base_url: str, *, timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS) -> None:
        httpx = _import_httpx()
        self.base_url = normalize_http_base_url(base_url)
        self.client = httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )
        self.experiment_id: str | None = None

    def set_experiment(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id

    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-Request-Id", f"p0-http-{uuid4().hex[:12]}")
        if isinstance(url, str) and url.startswith("/api/v1/experiments/") and self.experiment_id is not None:
            headers.setdefault("X-Experiment-Id", self.experiment_id)
        kwargs["headers"] = headers
        return self.client.request(method, url, **kwargs)

    def get(self, url: str, **kwargs):
        return self.request("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self.request("POST", url, **kwargs)

    def put(self, url: str, **kwargs):
        return self.request("PUT", url, **kwargs)

    def close(self) -> None:
        self.client.close()


def run_p0_benchmark_suite(
    *,
    base_url: str | None = None,
    report_path: str | None = None,
    output_json_path: str | None = None,
    concurrency_levels: tuple[int, ...] = DEFAULT_CONCURRENCY_LEVELS,
    mixed_concurrency: int = DEFAULT_MIXED_CONCURRENCY,
    full_flow_runs: int = DEFAULT_FULL_FLOW_RUNS,
    capacity_batch_sizes: tuple[int, ...] = DEFAULT_CAPACITY_BATCH_SIZES,
    decision_interval_seconds: float = DEFAULT_DECISION_INTERVAL_SECONDS,
    decision_periodic_batches: int = DEFAULT_DECISION_PERIODIC_BATCHES,
    http_timeout_seconds: float = DEFAULT_HTTP_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """运行 P0 压测套件并输出 Markdown / JSON 报告。"""

    settings = get_settings()
    normalized_base_url = resolve_benchmark_base_url(base_url)
    report_target, json_target = _resolve_suite_output_targets(
        report_path=report_path,
        output_json_path=output_json_path,
    )
    baseline_report_target = json_target.parent / "p0-baseline-report.md"
    baseline_json_target = json_target.parent / "p0-baseline-latest.json"
    timestamp = datetime.now(UTC)

    sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
    sqlalchemy_engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    original_sqlalchemy_level = sqlalchemy_logger.level
    original_sqlalchemy_engine_level = sqlalchemy_engine_logger.level
    sqlalchemy_logger.setLevel(logging.WARNING)
    sqlalchemy_engine_logger.setLevel(logging.WARNING)
    muted_loggers = {
        "httpx": logging.WARNING,
        "httpcore": logging.WARNING,
        "asyncio": logging.WARNING,
        "backend.app.main": logging.WARNING,
    }
    original_muted_levels = {name: logging.getLogger(name).level for name in muted_loggers}
    for name, level in muted_loggers.items():
        logging.getLogger(name).setLevel(level)

    try:
        _assert_http_server_ready(normalized_base_url, timeout_seconds=http_timeout_seconds)

        baseline_result = formal_service.run_formal_benchmark(
            report_path=str(baseline_report_target),
            output_json_path=str(baseline_json_target),
        )
        _cleanup_benchmark_artifacts(
            experiment_id=baseline_result["experiment_id"],
            type_ids=list((baseline_result.get("dataset") or {}).get("crowd_types", {}).values()),
        )

        scenarios = [
            {
                "key": "baseline_app",
                "display_name": "单机应用层基线压测",
                "description": "复用现有 formal benchmark，继续保留为回归基线。",
                "transport": "inprocess",
                "metrics": list(baseline_result["metrics"]),
                "dataset": baseline_result["dataset"],
                "metadata": {
                    "report_path": str(baseline_report_target),
                    "json_path": str(baseline_json_target),
                },
            },
            _run_real_http_single_interface_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
            ),
            _run_http_concurrency_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                concurrency_levels=concurrency_levels,
            ),
            _run_periodic_decision_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                interval_seconds=decision_interval_seconds,
                batch_count=decision_periodic_batches,
            ),
            _run_mixed_traffic_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                mixed_concurrency=mixed_concurrency,
            ),
            _run_full_flow_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                runs=full_flow_runs,
            ),
            _run_maintenance_chain_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                runs=full_flow_runs,
            ),
            _run_trajectory_capacity_curve_scenario(
                base_url=normalized_base_url,
                timeout_seconds=http_timeout_seconds,
                batch_sizes=capacity_batch_sizes,
            ),
        ]

        results = {
            "generated_at": timestamp.isoformat(),
            "suite_key": "benchmark_p0",
            "http_base_url": normalized_base_url,
            "environment": {
                **formal_service._collect_environment(settings=settings),
                "http_base_url": normalized_base_url,
                "concurrency_levels": list(concurrency_levels),
                "mixed_concurrency": mixed_concurrency,
                "full_flow_runs": full_flow_runs,
                "capacity_batch_sizes": list(capacity_batch_sizes),
                "decision_interval_seconds": decision_interval_seconds,
                "decision_periodic_batches": decision_periodic_batches,
                "http_timeout_seconds": http_timeout_seconds,
            },
            "scenarios": scenarios,
        }
        results["summary"] = _build_suite_summary(results)

        json_target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        report_target.write_text(_build_suite_report(results), encoding="utf-8")
        logger.info("P0 压测套件完成: report=%s json=%s", report_target, json_target)

        return {
            "generated_at": results["generated_at"],
            "http_base_url": normalized_base_url,
            "report_path": str(report_target),
            "json_path": str(json_target),
            "summary": results["summary"],
        }
    finally:
        sqlalchemy_logger.setLevel(original_sqlalchemy_level)
        sqlalchemy_engine_logger.setLevel(original_sqlalchemy_engine_level)
        for name, level in original_muted_levels.items():
            logging.getLogger(name).setLevel(level)


def parse_positive_int_tuple(raw: str | None, *, fallback: tuple[int, ...]) -> tuple[int, ...]:
    """把逗号分隔的正整数列表解析成 tuple。"""

    if raw is None or not raw.strip():
        return fallback

    values: list[int] = []
    for part in raw.split(","):
        stripped = part.strip()
        if not stripped:
            continue
        value = int(stripped)
        if value <= 0:
            raise ValueError(f"压测参数必须为正整数: {raw}")
        if value not in values:
            values.append(value)
    if not values:
        raise ValueError(f"压测参数不能为空: {raw}")
    return tuple(values)


def normalize_http_base_url(base_url: str) -> str:
    """标准化 HTTP 基地址。"""

    normalized = base_url.strip()
    if not normalized:
        raise ValueError("HTTP 基地址不能为空")
    if not normalized.startswith(("http://", "https://")):
        normalized = f"http://{normalized}"

    parsed = urlparse(normalized)
    host = parsed.hostname or ""
    if host in {"0.0.0.0", "::"}:
        replacement = "127.0.0.1"
        netloc = parsed.netloc.replace(host, replacement)
        normalized = parsed._replace(netloc=netloc).geturl()
    return normalized.rstrip("/")


def resolve_benchmark_base_url(base_url: str | None) -> str:
    """根据显式入参或配置文件推导压测基地址。"""

    if base_url is not None and base_url.strip():
        return normalize_http_base_url(base_url)

    settings = get_settings()
    host = settings.host.strip() or "127.0.0.1"
    if host in {"0.0.0.0", "::"}:
        host = "127.0.0.1"
    return normalize_http_base_url(f"http://{host}:{settings.port}")


def build_mixed_operation_plan(concurrency: int) -> list[str]:
    """为混合流量场景生成一轮操作分布。"""

    if concurrency <= 0:
        raise ValueError("mixed_concurrency 必须大于 0")

    weights = [
        ("decision", 3),
        ("trajectory", 3),
        ("stats", 2),
        ("heatmap", 2),
    ]
    counts = {name: 0 for name, _ in weights}
    plan: list[str] = []
    for _ in range(concurrency):
        operation = max(
            weights,
            key=lambda item: item[1] / (counts[item[0]] + 1),
        )[0]
        counts[operation] += 1
        plan.append(operation)
    return plan


def build_periodic_schedule_offsets(*, interval_seconds: float, batch_count: int) -> list[float]:
    """构造固定节拍的请求发送偏移。"""

    if interval_seconds <= 0:
        raise ValueError("interval_seconds 必须大于 0")
    if batch_count <= 0:
        raise ValueError("batch_count 必须大于 0")
    return [round(index * interval_seconds, 6) for index in range(batch_count)]


def scale_type_counts(total_agents: int) -> dict[str, int]:
    """按 formal benchmark 的默认比例缩放人群数量。"""

    if total_agents <= 0:
        raise ValueError("total_agents 必须大于 0")

    base_total = sum(formal_service.TYPE_COUNTS.values())
    ordered_codes = [type_code for type_code, *_ in formal_service.TYPE_BLUEPRINTS]
    remaining = total_agents
    result: dict[str, int] = {}
    for index, type_code in enumerate(ordered_codes):
        if index == len(ordered_codes) - 1:
            result[type_code] = remaining
            break
        count = round(total_agents * formal_service.TYPE_COUNTS[type_code] / base_total)
        remaining -= count
        result[type_code] = count
    return result


def _run_real_http_single_interface_scenario(*, base_url: str, timeout_seconds: float) -> dict[str, Any]:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    context: formal_service.BenchmarkExperimentContext | None = None
    try:
        context = formal_service._prepare_benchmark_experiment(client=client, suffix=_benchmark_suffix("http"))
        spawn_metric = formal_service._benchmark_spawn_positions(client=client, context=context)
        formal_service._finalize_spawn_and_start_experiment(client=client, context=context)
        decision_metric = formal_service._benchmark_decision_batches(client=client, context=context)
        trajectory_metric = formal_service._benchmark_trajectory_ingest(client=client, context=context)
        formal_service._seed_runtime_history(context=context)
        formal_service._refresh_analytics(context=context)
        analytics_metrics = formal_service._benchmark_analytics_endpoints(client=client, context=context)
        crowd_metric = formal_service._benchmark_crowd_types_list(client=client)
        metrics = [crowd_metric, spawn_metric, decision_metric, trajectory_metric, *analytics_metrics]
        return {
            "key": "real_http_single",
            "display_name": "真实 HTTP 单接口压测",
            "description": "对真实启动中的 HTTP 服务执行核心接口压测，覆盖网络与 Uvicorn 链路。",
            "transport": "http",
            "metrics": [metric.to_dict() for metric in metrics],
            "dataset": formal_service._build_dataset_summary(context=context),
            "metadata": {
                "experiment_id": context.experiment_id,
                "http_base_url": base_url,
            },
        }
    finally:
        if context is not None:
            _cleanup_benchmark_artifacts(
                experiment_id=context.experiment_id,
                type_ids=list(context.type_ids.values()),
            )
        client.close()


def _run_http_concurrency_scenario(
    *,
    base_url: str,
    timeout_seconds: float,
    concurrency_levels: tuple[int, ...],
) -> dict[str, Any]:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    context: formal_service.BenchmarkExperimentContext | None = None
    try:
        context = formal_service._prepare_benchmark_experiment(client=client, suffix=_benchmark_suffix("conc"))
        spawn_metric = formal_service._benchmark_spawn_positions(client=client, context=context)
        formal_service._finalize_spawn_and_start_experiment(client=client, context=context)
        formal_service._seed_runtime_history(context=context)
        formal_service._refresh_analytics(context=context)
        decision_targets = formal_service._build_agent_target_map(context)
        heatmap_payload = _build_heatmap_payload()
        client.post(
            f"/api/v1/experiments/{context.experiment_id}/heatmap",
            json=heatmap_payload,
        )

        metrics: list[formal_service.BenchmarkMetric] = [spawn_metric]
        for concurrency in concurrency_levels:
            metrics.append(
                _benchmark_concurrency_metric(
                    key=f"http_concurrent_decisions_c{concurrency}",
                    display_name=f"并发决策批次（{concurrency} 并发）",
                    concurrency=concurrency,
                    rounds=3,
                    timeout_seconds=timeout_seconds,
                    base_url=base_url,
                    notes=["真实 HTTP 并发测量，每个请求都使用独立 request id 与 batch id。"],
                    request_specs_factory=lambda round_index, c=concurrency: [
                        _build_decision_request_spec(
                            context=context,
                            decision_targets=decision_targets,
                            batch_id=f"p0_decision_c{c}_r{round_index}_{index}_{uuid4().hex[:6]}",
                            simulation_time_ms=600_000 + round_index * 10_000 + index * 100,
                        )
                        for index in range(c)
                    ],
                )
            )
            metrics.append(
                _benchmark_concurrency_metric(
                    key=f"http_concurrent_trajectories_c{concurrency}",
                    display_name=f"并发轨迹上报（{concurrency} 并发）",
                    concurrency=concurrency,
                    rounds=3,
                    timeout_seconds=timeout_seconds,
                    base_url=base_url,
                    notes=["每个请求默认上报 2000 点。"],
                    request_specs_factory=lambda round_index, c=concurrency: [
                        _build_trajectory_request_spec(
                            context=context,
                            decision_targets=decision_targets,
                            batch_id=f"p0_traj_c{c}_r{round_index}_{index}_{uuid4().hex[:6]}",
                            simulation_time_ms=700_000 + round_index * 10_000 + index * 100,
                            progress_ratio=0.20 + (index % 4) * 0.05,
                            batch_size=2000,
                        )
                        for index in range(c)
                    ],
                )
            )
            metrics.append(
                _benchmark_concurrency_metric(
                    key=f"http_concurrent_stats_c{concurrency}",
                    display_name=f"并发统计查询（{concurrency} 并发）",
                    concurrency=concurrency,
                    rounds=3,
                    timeout_seconds=timeout_seconds,
                    base_url=base_url,
                    notes=["真实 HTTP 并发 GET /stats。"],
                    request_specs_factory=lambda _round_index, c=concurrency: [
                        {
                            "method": "GET",
                            "url": f"/api/v1/experiments/{context.experiment_id}/stats",
                            "expect_status": 200,
                            "experiment_id": context.experiment_id,
                            "extra": {"operation": "stats"},
                        }
                        for _ in range(c)
                    ],
                )
            )
            metrics.append(
                _benchmark_concurrency_metric(
                    key=f"http_concurrent_heatmap_c{concurrency}",
                    display_name=f"并发热力图查询（{concurrency} 并发，热缓存）",
                    concurrency=concurrency,
                    rounds=3,
                    timeout_seconds=timeout_seconds,
                    base_url=base_url,
                    notes=["测量前会先预热一次热力图缓存，使结果更接近日常 dashboard 热读。"],
                    request_specs_factory=lambda _round_index, c=concurrency: [
                        {
                            "method": "POST",
                            "url": f"/api/v1/experiments/{context.experiment_id}/heatmap",
                            "expect_status": 200,
                            "experiment_id": context.experiment_id,
                            "json": dict(heatmap_payload),
                            "extra": {"operation": "heatmap"},
                        }
                        for _ in range(c)
                    ],
                )
            )

        return {
            "key": "http_concurrency",
            "display_name": "并发阶梯压测",
            "description": "对真实 HTTP 服务做分档并发压测，观察核心接口在不同并发度下的退化曲线。",
            "transport": "http",
            "metrics": [metric.to_dict() for metric in metrics],
            "dataset": formal_service._build_dataset_summary(context=context),
            "metadata": {
                "experiment_id": context.experiment_id,
                "concurrency_levels": list(concurrency_levels),
            },
        }
    finally:
        if context is not None:
            _cleanup_benchmark_artifacts(
                experiment_id=context.experiment_id,
                type_ids=list(context.type_ids.values()),
            )
        client.close()


def _run_periodic_decision_scenario(
    *,
    base_url: str,
    timeout_seconds: float,
    interval_seconds: float,
    batch_count: int,
) -> dict[str, Any]:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    context: formal_service.BenchmarkExperimentContext | None = None
    try:
        context = formal_service._prepare_benchmark_experiment(client=client, suffix=_benchmark_suffix("periodic"))
        formal_service._benchmark_spawn_positions(client=client, context=context)
        formal_service._finalize_spawn_and_start_experiment(client=client, context=context)
        decision_targets = formal_service._build_agent_target_map(context)

        schedule_offsets = build_periodic_schedule_offsets(
            interval_seconds=interval_seconds,
            batch_count=batch_count,
        )
        request_specs = [
            {
                **_build_decision_request_spec(
                    context=context,
                    decision_targets=decision_targets,
                    batch_id=f"p0_periodic_decision_{index}_{uuid4().hex[:6]}",
                    simulation_time_ms=1_000_000 + index * int(interval_seconds * 1000),
                ),
                "expect_statuses": (200, 409),
                "scheduled_offset_seconds": scheduled_offset,
                "extra": {
                    "operation": "decision_periodic",
                    "batch_index": index + 1,
                    "scheduled_offset_seconds": scheduled_offset,
                },
            }
            for index, scheduled_offset in enumerate(schedule_offsets)
        ]
        samples = _run_periodic_async_requests(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            request_specs=request_specs,
        )

        success_samples = [sample for sample in samples if sample.status_code == 200]
        conflict_samples = [sample for sample in samples if sample.status_code == 409]
        drift_samples = [
            formal_service.BenchmarkSample(
                duration_ms=round(float(sample.extra.get("schedule_drift_ms", 0.0)), 2),
                status_code=sample.status_code,
                extra={
                    "batch_index": sample.extra.get("batch_index"),
                    "scheduled_offset_seconds": sample.extra.get("scheduled_offset_seconds"),
                    "actual_start_offset_ms": sample.extra.get("actual_start_offset_ms"),
                    "status_code": sample.status_code,
                },
            )
            for sample in samples
        ]
        max_drift_ms = max((float(sample.extra.get("schedule_drift_ms", 0.0)) for sample in samples), default=0.0)
        overlap_count = sum(1 for sample in samples if float(sample.extra.get("schedule_drift_ms", 0.0)) >= interval_seconds * 1000)
        notes = [
            f"固定节拍：每 {interval_seconds:.2f} 秒发送 1 批决策，共 {batch_count} 批。",
            "调度器按真实 wall clock 时间发请求，上一批未完成时不会等待，因此会自然暴露批次重叠和 409 冲突。",
            f"成功批次：{len(success_samples)}，冲突批次：{len(conflict_samples)}。",
            f"最大调度漂移：{max_drift_ms:.2f} ms；漂移超过一个周期的批次：{overlap_count}。",
        ]
        metrics = [
            formal_service.BenchmarkMetric(
                key="decision_periodic_5s_all",
                display_name=f"周期决策调度（每 {interval_seconds:.0f} 秒 1 批）",
                unit="ms",
                warmup_runs=0,
                measured_runs=len(samples),
                samples=samples,
                threshold=None,
                notes=notes,
            ),
            formal_service.BenchmarkMetric(
                key="decision_periodic_5s_success",
                display_name=f"周期决策成功批次（每 {interval_seconds:.0f} 秒 1 批）",
                unit="ms",
                warmup_runs=0,
                measured_runs=len(success_samples),
                samples=success_samples,
                threshold=None,
                notes=["只统计 status=200 的成功批次耗时。"],
            ),
            formal_service.BenchmarkMetric(
                key="decision_periodic_schedule_drift",
                display_name=f"周期决策调度漂移（每 {interval_seconds:.0f} 秒 1 批）",
                unit="ms",
                warmup_runs=0,
                measured_runs=len(drift_samples),
                samples=drift_samples,
                threshold=None,
                notes=["duration_ms 表示实际发起时间相对计划时间的漂移毫秒数，越低越稳定。"],
            ),
        ]
        return {
            "key": "decision_periodic",
            "display_name": "周期性决策压测",
            "description": "按真实 wall clock 每隔 5 秒发送一批决策请求，验证调度节拍、批次重叠和冲突风险。",
            "transport": "http+periodic",
            "metrics": [metric.to_dict() for metric in metrics],
            "dataset": {
                "experiment_id": context.experiment_id,
                "scheduled_batches": batch_count,
                "successful_batches": len(success_samples),
                "conflict_batches": len(conflict_samples),
            },
            "metadata": {
                "experiment_id": context.experiment_id,
                "interval_seconds": interval_seconds,
                "batch_count": batch_count,
                "scheduled_offsets_seconds": schedule_offsets,
            },
        }
    finally:
        if context is not None:
            _cleanup_benchmark_artifacts(
                experiment_id=context.experiment_id,
                type_ids=list(context.type_ids.values()),
            )
        client.close()


def _run_mixed_traffic_scenario(
    *,
    base_url: str,
    timeout_seconds: float,
    mixed_concurrency: int,
) -> dict[str, Any]:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    context: formal_service.BenchmarkExperimentContext | None = None
    try:
        context = formal_service._prepare_benchmark_experiment(client=client, suffix=_benchmark_suffix("mix"))
        formal_service._benchmark_spawn_positions(client=client, context=context)
        formal_service._finalize_spawn_and_start_experiment(client=client, context=context)
        formal_service._seed_runtime_history(context=context)
        formal_service._refresh_analytics(context=context)
        decision_targets = formal_service._build_agent_target_map(context)
        heatmap_payload = _build_heatmap_payload()
        client.post(
            f"/api/v1/experiments/{context.experiment_id}/heatmap",
            json=heatmap_payload,
        )

        plan = build_mixed_operation_plan(mixed_concurrency)
        grouped_samples: dict[str, list[formal_service.BenchmarkSample]] = defaultdict(list)
        overall_samples: list[formal_service.BenchmarkSample] = []

        for round_index in range(3):
            request_specs: list[dict[str, Any]] = []
            op_counters = defaultdict(int)
            for operation in plan:
                op_counters[operation] += 1
                request_specs.append(
                    _build_mixed_request_spec(
                        operation=operation,
                        context=context,
                        decision_targets=decision_targets,
                        heatmap_payload=heatmap_payload,
                        round_index=round_index,
                        ordinal=op_counters[operation],
                    )
                )
            round_started = perf_counter()
            round_samples = _run_async_requests(
                base_url=base_url,
                timeout_seconds=timeout_seconds,
                request_specs=request_specs,
            )
            throughput = _calculate_throughput(
                sample_count=len(round_samples),
                wall_elapsed_ms=(perf_counter() - round_started) * 1000,
            )
            for sample in round_samples:
                sample.extra["round"] = round_index + 1
                sample.extra["mixed_concurrency"] = mixed_concurrency
                sample.extra["throughput_rps"] = throughput
                grouped_samples[str(sample.extra["operation"])].append(sample)
                overall_samples.append(sample)

        metrics = [
            formal_service.BenchmarkMetric(
                key="mixed_overall",
                display_name=f"混合流量总览（{mixed_concurrency} 并发）",
                unit="ms",
                warmup_runs=0,
                measured_runs=len(overall_samples),
                samples=overall_samples,
                threshold=None,
                notes=["汇总决策、轨迹、统计、热力图四类请求的混合流量结果。"],
            )
        ]
        display_names = {
            "decision": "混合流量 - 决策批次",
            "trajectory": "混合流量 - 轨迹上报",
            "stats": "混合流量 - 统计查询",
            "heatmap": "混合流量 - 热力图查询",
        }
        for operation, samples in grouped_samples.items():
            metrics.append(
                formal_service.BenchmarkMetric(
                    key=f"mixed_{operation}",
                    display_name=display_names.get(operation, operation),
                    unit="ms",
                    warmup_runs=0,
                    measured_runs=len(samples),
                    samples=samples,
                    threshold=None,
                    notes=[f"混合流量场景中 operation={operation} 的子集结果。"],
                )
            )

        return {
            "key": "mixed_traffic",
            "display_name": "混合流量压测",
            "description": "模拟运行期写入与查询同时发生的真实负载，而不是只压单接口。",
            "transport": "http",
            "metrics": [metric.to_dict() for metric in metrics],
            "dataset": formal_service._build_dataset_summary(context=context),
            "metadata": {
                "experiment_id": context.experiment_id,
                "mixed_concurrency": mixed_concurrency,
                "mix_plan": plan,
            },
        }
    finally:
        if context is not None:
            _cleanup_benchmark_artifacts(
                experiment_id=context.experiment_id,
                type_ids=list(context.type_ids.values()),
            )
        client.close()


def _run_full_flow_scenario(*, base_url: str, timeout_seconds: float, runs: int) -> dict[str, Any]:
    samples_by_stage: dict[str, list[formal_service.BenchmarkSample]] = defaultdict(list)
    experiment_ids: list[str] = []

    for run_index in range(runs):
        client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
        context: formal_service.BenchmarkExperimentContext | None = None
        try:
            total_started = perf_counter()

            setup_started = perf_counter()
            context = formal_service._prepare_benchmark_experiment(
                client=client,
                suffix=_benchmark_suffix(f"flow{run_index + 1}"),
            )
            setup_elapsed_ms = (perf_counter() - setup_started) * 1000
            experiment_ids.append(context.experiment_id)
            _append_duration_sample(
                samples_by_stage,
                stage="full_flow_setup",
                duration_ms=setup_elapsed_ms,
                run_index=run_index,
                extra={"experiment_id": context.experiment_id},
            )

            spawn_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
                    json={"seed": 707 + run_index},
                ),
                expect_status=200,
                extra_builder=lambda response, _elapsed_ms: {
                    "generated_count": int(response.json()["data"]["total_count"]),
                },
            )
            spawned_agents = formal_service._normalize_spawned_agents(spawn_sample.extra.pop("_agents", []))
            context.spawned_agents = spawned_agents
            _append_existing_sample(samples_by_stage, "full_flow_spawn", spawn_sample, run_index=run_index)

            start_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/start",
                    json={"actual_start_time_ms": 1700001000000 + run_index * 1000},
                ),
                expect_status=200,
            )
            _append_existing_sample(samples_by_stage, "full_flow_start", start_sample, run_index=run_index)

            decision_targets = formal_service._build_agent_target_map(context)
            decision_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/decisions",
                    json=formal_service._build_decision_request_payload(
                        context=context,
                        benchmark_agents=context.spawned_agents[:100],
                        decision_targets=decision_targets,
                        batch_id=f"p0_full_flow_decision_{run_index}_{uuid4().hex[:6]}",
                        simulation_time_ms=500_000 + run_index * 1000,
                    ),
                ),
                expect_status=200,
            )
            _append_existing_sample(samples_by_stage, "full_flow_decision", decision_sample, run_index=run_index)

            trajectory_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/trajectories",
                    json=formal_service._build_trajectory_payload(
                        benchmark_agents=context.spawned_agents[:2000],
                        decision_targets=decision_targets,
                        batch_id=f"p0_full_flow_traj_{run_index}_{uuid4().hex[:6]}",
                        simulation_time_ms=520_000 + run_index * 1000,
                        progress_ratio=0.26,
                    ),
                ),
                expect_status=200,
                extra_builder=lambda response, elapsed_ms: formal_service._build_trajectory_ingest_extra(
                    response=response,
                    elapsed_ms=elapsed_ms,
                    batch_id=f"p0_full_flow_traj_{run_index}",
                ),
            )
            _append_existing_sample(samples_by_stage, "full_flow_trajectory", trajectory_sample, run_index=run_index)

            complete_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/complete",
                    json={
                        "reason": CompletionReason.SIMULATION_FINISHED.value,
                        "actual_duration_ms": 600_000 + run_index * 1000,
                        "final_agent_count": len(context.spawned_agents),
                    },
                ),
                expect_status=200,
            )
            _append_existing_sample(samples_by_stage, "full_flow_complete", complete_sample, run_index=run_index)

            total_elapsed_ms = (perf_counter() - total_started) * 1000
            _append_duration_sample(
                samples_by_stage,
                stage="full_flow_total",
                duration_ms=total_elapsed_ms,
                run_index=run_index,
                extra={"experiment_id": context.experiment_id},
            )

            drain_result = _drain_maintenance_jobs()
            logger.info(
                "完整实验闭环压测已完成维护任务收尾: experiment_id=%s worker_backend=%s duration_ms=%s",
                context.experiment_id,
                drain_result["worker_backend"],
                drain_result["duration_ms"],
            )
        finally:
            if context is not None:
                _cleanup_benchmark_artifacts(
                    experiment_id=context.experiment_id,
                    type_ids=list(context.type_ids.values()),
                )
            client.close()

    metrics = [
        formal_service.BenchmarkMetric(
            key=stage,
            display_name=display_name,
            unit="ms",
            warmup_runs=0,
            measured_runs=len(samples),
            samples=samples,
            threshold=None,
            notes=["每个 run 在 complete 之后都会额外 drain maintenance jobs，避免队列残留。"],
        )
        for stage, display_name, samples in [
            ("full_flow_setup", "闭环链路 - 启动前配置", samples_by_stage["full_flow_setup"]),
            ("full_flow_spawn", "闭环链路 - 生成出生点", samples_by_stage["full_flow_spawn"]),
            ("full_flow_start", "闭环链路 - 启动实验", samples_by_stage["full_flow_start"]),
            ("full_flow_decision", "闭环链路 - 决策批次", samples_by_stage["full_flow_decision"]),
            ("full_flow_trajectory", "闭环链路 - 轨迹上报", samples_by_stage["full_flow_trajectory"]),
            ("full_flow_complete", "闭环链路 - 完成实验", samples_by_stage["full_flow_complete"]),
            ("full_flow_total", "闭环链路 - 端到端总耗时", samples_by_stage["full_flow_total"]),
        ]
        if samples
    ]
    return {
        "key": "full_flow",
        "display_name": "完整实验闭环压测",
        "description": "覆盖创建实验到完成实验的整条主业务链路，而不只测中间热点接口。",
        "transport": "http",
        "metrics": [metric.to_dict() for metric in metrics],
        "dataset": {
            "runs": runs,
            "experiment_ids": experiment_ids,
        },
        "metadata": {
            "runs": runs,
        },
    }


def _run_maintenance_chain_scenario(*, base_url: str, timeout_seconds: float, runs: int) -> dict[str, Any]:
    samples_by_stage: dict[str, list[formal_service.BenchmarkSample]] = defaultdict(list)
    worker_backend: str | None = None

    for run_index in range(runs):
        client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
        context: formal_service.BenchmarkExperimentContext | None = None
        try:
            context = formal_service._prepare_benchmark_experiment(
                client=client,
                suffix=_benchmark_suffix(f"maint{run_index + 1}"),
            )
            spawn_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
                    json={"seed": 909 + run_index},
                ),
                expect_status=200,
            )
            context.spawned_agents = formal_service._normalize_spawned_agents(spawn_sample.extra.pop("_agents", []))
            formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/start",
                    json={"actual_start_time_ms": 1700002000000 + run_index * 1000},
                ),
                expect_status=200,
            )
            decision_targets = formal_service._build_agent_target_map(context)
            for batch_index in range(3):
                formal_service._measure_http_call(
                    operation=lambda batch_value=batch_index: client.post(
                        f"/api/v1/experiments/{context.experiment_id}/decisions",
                        json=formal_service._build_decision_request_payload(
                            context=context,
                            benchmark_agents=context.spawned_agents[:100],
                            decision_targets=decision_targets,
                            batch_id=f"p0_maintenance_decision_{run_index}_{batch_value}_{uuid4().hex[:6]}",
                            simulation_time_ms=620_000 + batch_value * 5_000,
                        ),
                    ),
                    expect_status=200,
                )
            for batch_index in range(3):
                formal_service._measure_http_call(
                    operation=lambda batch_value=batch_index: client.post(
                        f"/api/v1/experiments/{context.experiment_id}/trajectories",
                        json=formal_service._build_trajectory_payload(
                            benchmark_agents=context.spawned_agents[:2000],
                            decision_targets=decision_targets,
                            batch_id=f"p0_maintenance_traj_{run_index}_{batch_value}_{uuid4().hex[:6]}",
                            simulation_time_ms=650_000 + batch_value * 1_000,
                            progress_ratio=0.20 + batch_value * 0.05,
                        ),
                    ),
                    expect_status=200,
                )

            complete_started = perf_counter()
            complete_sample = formal_service._measure_http_call(
                operation=lambda: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/complete",
                    json={
                        "reason": CompletionReason.SIMULATION_FINISHED.value,
                        "actual_duration_ms": 660_000 + run_index * 1_000,
                        "final_agent_count": len(context.spawned_agents),
                    },
                ),
                expect_status=200,
            )
            _append_existing_sample(
                samples_by_stage,
                "maintenance_complete",
                complete_sample,
                run_index=run_index,
                extra={
                    "job_counts_before": _count_maintenance_jobs_by_status(context.experiment_id),
                },
            )

            worker_started = perf_counter()
            drain_result = _drain_maintenance_jobs()
            worker_elapsed_ms = (perf_counter() - worker_started) * 1000
            worker_backend = str(drain_result["worker_backend"])
            _append_duration_sample(
                samples_by_stage,
                stage="maintenance_worker",
                duration_ms=worker_elapsed_ms,
                run_index=run_index,
                extra={
                    "worker_backend": worker_backend,
                    "worker_result": drain_result,
                    "job_counts_after": _count_maintenance_jobs_by_status(context.experiment_id),
                },
            )
            _append_duration_sample(
                samples_by_stage,
                stage="maintenance_end_to_end",
                duration_ms=(perf_counter() - complete_started) * 1000,
                run_index=run_index,
                extra={"worker_backend": worker_backend},
            )
        finally:
            if context is not None:
                _cleanup_benchmark_artifacts(
                    experiment_id=context.experiment_id,
                    type_ids=list(context.type_ids.values()),
                )
            client.close()

    metrics = [
        formal_service.BenchmarkMetric(
            key=stage,
            display_name=display_name,
            unit="ms",
            warmup_runs=0,
            measured_runs=len(samples),
            samples=samples,
            threshold=None,
            notes=[f"worker_backend={worker_backend or 'unknown'}"],
        )
        for stage, display_name, samples in [
            ("maintenance_complete", "维护链路 - complete 接口", samples_by_stage["maintenance_complete"]),
            ("maintenance_worker", "维护链路 - worker 处理耗时", samples_by_stage["maintenance_worker"]),
            ("maintenance_end_to_end", "维护链路 - complete 到 worker 完成", samples_by_stage["maintenance_end_to_end"]),
        ]
        if samples
    ]
    return {
        "key": "maintenance_chain",
        "display_name": "真实维护链路压测",
        "description": "覆盖 complete 之后的 maintenance jobs 与 worker 消费链路，避免再直接调私有刷新函数。",
        "transport": "http+worker",
        "metrics": [metric.to_dict() for metric in metrics],
        "dataset": {"runs": runs},
        "metadata": {"runs": runs, "worker_backend": worker_backend},
    }


def _run_trajectory_capacity_curve_scenario(
    *,
    base_url: str,
    timeout_seconds: float,
    batch_sizes: tuple[int, ...],
) -> dict[str, Any]:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    context: formal_service.BenchmarkExperimentContext | None = None
    try:
        context = formal_service._prepare_benchmark_experiment(client=client, suffix=_benchmark_suffix("capacity"))
        _override_allocation_counts(
            client=client,
            context=context,
            total_agents=max(batch_sizes),
            suffix=_benchmark_suffix("capalloc"),
        )
        spawn_sample = formal_service._measure_http_call(
            operation=lambda: client.post(
                f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
                json={"seed": 1212},
            ),
            expect_status=200,
        )
        context.spawned_agents = formal_service._normalize_spawned_agents(spawn_sample.extra.pop("_agents", []))
        formal_service._measure_http_call(
            operation=lambda: client.post(
                f"/api/v1/experiments/{context.experiment_id}/start",
                json={"actual_start_time_ms": 1700003000000},
            ),
            expect_status=200,
        )
        decision_targets = formal_service._build_agent_target_map(context)

        metrics: list[formal_service.BenchmarkMetric] = []
        for batch_size in batch_sizes:
            if batch_size > len(context.spawned_agents):
                raise RuntimeError(f"轨迹容量压测配置无效: batch_size={batch_size} available={len(context.spawned_agents)}")

            benchmark_agents = context.spawned_agents[:batch_size]
            warmup_payload = formal_service._build_trajectory_payload(
                benchmark_agents=benchmark_agents,
                decision_targets=decision_targets,
                batch_id=f"p0_capacity_warmup_{batch_size}_{uuid4().hex[:6]}",
                simulation_time_ms=800_000 + batch_size,
                progress_ratio=0.18,
            )
            formal_service._measure_http_call(
                operation=lambda payload_value=warmup_payload: client.post(
                    f"/api/v1/experiments/{context.experiment_id}/trajectories",
                    json=payload_value,
                ),
                expect_status=200,
            )

            samples: list[formal_service.BenchmarkSample] = []
            for round_index in range(3):
                batch_id = f"p0_capacity_{batch_size}_{round_index}_{uuid4().hex[:6]}"
                payload = formal_service._build_trajectory_payload(
                    benchmark_agents=benchmark_agents,
                    decision_targets=decision_targets,
                    batch_id=batch_id,
                    simulation_time_ms=810_000 + batch_size * 10 + round_index * 1_000,
                    progress_ratio=0.24 + round_index * 0.04,
                )
                sample = formal_service._measure_http_call(
                    operation=lambda payload_value=payload: client.post(
                        f"/api/v1/experiments/{context.experiment_id}/trajectories",
                        json=payload_value,
                    ),
                    expect_status=200,
                    extra_builder=lambda response, elapsed_ms, batch_value=batch_id: {
                        **formal_service._build_trajectory_ingest_extra(
                            response=response,
                            elapsed_ms=elapsed_ms,
                            batch_id=batch_value,
                        ),
                        "batch_size": batch_size,
                        "round": round_index + 1,
                    },
                )
                samples.append(sample)

            notes = [f"轨迹上报容量曲线，当前批次大小={batch_size}。"]
            threshold = formal_service.PDF_THRESHOLDS["trajectory_ingest_2000"] if batch_size == 2000 else None
            if batch_size == 2000:
                notes.append("2000 点批次仍沿用 PDF 9.4 的正式阈值，方便和历史结果对照。")
            metrics.append(
                formal_service.BenchmarkMetric(
                    key=f"trajectory_capacity_{batch_size}",
                    display_name=f"轨迹上报容量曲线（{batch_size} 点批次）",
                    unit="ms",
                    warmup_runs=1,
                    measured_runs=len(samples),
                    samples=samples,
                    threshold=threshold,
                    notes=notes,
                )
            )

        return {
            "key": "trajectory_capacity_curve",
            "display_name": "轨迹上报容量曲线",
            "description": "按不同点批次压轨迹写入，找到当前吞吐拐点，而不是只盯 2000 点单一档位。",
            "transport": "http",
            "metrics": [metric.to_dict() for metric in metrics],
            "dataset": {
                "experiment_id": context.experiment_id,
                "total_agents": len(context.spawned_agents),
                "batch_sizes": list(batch_sizes),
            },
            "metadata": {"batch_sizes": list(batch_sizes)},
        }
    finally:
        if context is not None:
            _cleanup_benchmark_artifacts(
                experiment_id=context.experiment_id,
                type_ids=list(context.type_ids.values()),
            )
        client.close()


def _benchmark_concurrency_metric(
    *,
    key: str,
    display_name: str,
    concurrency: int,
    rounds: int,
    timeout_seconds: float,
    base_url: str,
    request_specs_factory,
    notes: list[str],
) -> formal_service.BenchmarkMetric:
    samples: list[formal_service.BenchmarkSample] = []
    for round_index in range(rounds):
        round_started = perf_counter()
        round_samples = _run_async_requests(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            request_specs=request_specs_factory(round_index),
        )
        throughput = _calculate_throughput(
            sample_count=len(round_samples),
            wall_elapsed_ms=(perf_counter() - round_started) * 1000,
        )
        for sample in round_samples:
            sample.extra["round"] = round_index + 1
            sample.extra["concurrency"] = concurrency
            sample.extra["throughput_rps"] = throughput
        samples.extend(round_samples)
    return formal_service.BenchmarkMetric(
        key=key,
        display_name=display_name,
        unit="ms",
        warmup_runs=0,
        measured_runs=len(samples),
        samples=samples,
        threshold=None,
        notes=[*notes, f"concurrency={concurrency}", f"rounds={rounds}"],
    )


def _run_async_requests(
    *,
    base_url: str,
    timeout_seconds: float,
    request_specs: list[dict[str, Any]],
) -> list[formal_service.BenchmarkSample]:
    return asyncio.run(
        _run_async_requests_inner(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            request_specs=request_specs,
        )
    )


def _run_periodic_async_requests(
    *,
    base_url: str,
    timeout_seconds: float,
    request_specs: list[dict[str, Any]],
) -> list[formal_service.BenchmarkSample]:
    return asyncio.run(
        _run_periodic_async_requests_inner(
            base_url=base_url,
            timeout_seconds=timeout_seconds,
            request_specs=request_specs,
        )
    )


async def _run_async_requests_inner(
    *,
    base_url: str,
    timeout_seconds: float,
    request_specs: list[dict[str, Any]],
) -> list[formal_service.BenchmarkSample]:
    httpx = _import_httpx()
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
    ) as client:
        return list(
            await asyncio.gather(
                *[
                    _perform_async_request(client=client, request_spec=request_spec)
                    for request_spec in request_specs
                ]
            )
        )


async def _run_periodic_async_requests_inner(
    *,
    base_url: str,
    timeout_seconds: float,
    request_specs: list[dict[str, Any]],
) -> list[formal_service.BenchmarkSample]:
    httpx = _import_httpx()
    async with httpx.AsyncClient(
        base_url=base_url,
        timeout=httpx.Timeout(timeout_seconds),
    ) as client:
        origin = asyncio.get_running_loop().time()
        return list(
            await asyncio.gather(
                *[
                    _perform_async_request_with_schedule(
                        client=client,
                        request_spec=request_spec,
                        schedule_origin_seconds=origin,
                    )
                    for request_spec in request_specs
                ]
            )
        )


async def _perform_async_request(*, client, request_spec: dict[str, Any]) -> formal_service.BenchmarkSample:
    return await _perform_async_request_with_schedule(
        client=client,
        request_spec=request_spec,
        schedule_origin_seconds=None,
    )


async def _perform_async_request_with_schedule(
    *,
    client,
    request_spec: dict[str, Any],
    schedule_origin_seconds: float | None,
) -> formal_service.BenchmarkSample:
    headers = dict(request_spec.get("headers", {}) or {})
    headers.setdefault("X-Request-Id", f"p0-async-{uuid4().hex[:12]}")
    experiment_id = request_spec.get("experiment_id")
    if experiment_id is not None and str(request_spec["url"]).startswith("/api/v1/experiments/"):
        headers.setdefault("X-Experiment-Id", str(experiment_id))

    loop = asyncio.get_running_loop()
    scheduled_offset_seconds = float(request_spec.get("scheduled_offset_seconds", 0.0))
    if schedule_origin_seconds is not None and scheduled_offset_seconds > 0:
        wait_seconds = schedule_origin_seconds + scheduled_offset_seconds - loop.time()
        if wait_seconds > 0:
            await asyncio.sleep(wait_seconds)
    actual_started_seconds = loop.time()
    started = perf_counter()
    response = await client.request(
        request_spec["method"],
        request_spec["url"],
        json=request_spec.get("json"),
        params=request_spec.get("params"),
        headers=headers,
    )
    elapsed_ms = (perf_counter() - started) * 1000
    expected_statuses = tuple(int(value) for value in request_spec.get("expect_statuses", (request_spec.get("expect_status", 200),)))
    if response.status_code not in expected_statuses:
        raise RuntimeError(
            f"异步压测请求失败: method={request_spec['method']} url={request_spec['url']} "
            f"status={response.status_code} expect={expected_statuses} body={response.text}"
        )

    extra = dict(request_spec.get("extra", {}) or {})
    if schedule_origin_seconds is not None:
        extra["actual_start_offset_ms"] = round((actual_started_seconds - schedule_origin_seconds) * 1000, 2)
        extra["schedule_drift_ms"] = round((actual_started_seconds - schedule_origin_seconds - scheduled_offset_seconds) * 1000, 2)
    extra_builder = request_spec.get("extra_builder")
    if extra_builder is not None:
        extra.update(extra_builder(response, elapsed_ms))
    if response.status_code != 200:
        extra["response_body_preview"] = response.text[:500]
    return formal_service.BenchmarkSample(
        duration_ms=round(elapsed_ms, 2),
        status_code=response.status_code,
        extra=extra,
    )


def _build_decision_request_spec(
    *,
    context: formal_service.BenchmarkExperimentContext,
    decision_targets: dict[str, dict[str, Any]],
    batch_id: str,
    simulation_time_ms: int,
) -> dict[str, Any]:
    payload = formal_service._build_decision_request_payload(
        context=context,
        benchmark_agents=context.spawned_agents[:100],
        decision_targets=decision_targets,
        batch_id=batch_id,
        simulation_time_ms=simulation_time_ms,
    )
    return {
        "method": "POST",
        "url": f"/api/v1/experiments/{context.experiment_id}/decisions",
        "expect_status": 200,
        "experiment_id": context.experiment_id,
        "json": payload,
        "extra": {"operation": "decision", "batch_id": batch_id},
        "extra_builder": lambda response, _elapsed_ms: {
            "total_count": int(response.json()["data"]["total_count"]),
            "success_count": int(response.json()["data"]["success_count"]),
            "fallback_count": int(response.json()["data"]["fallback_count"]),
        },
    }


def _build_trajectory_request_spec(
    *,
    context: formal_service.BenchmarkExperimentContext,
    decision_targets: dict[str, dict[str, Any]],
    batch_id: str,
    simulation_time_ms: int,
    progress_ratio: float,
    batch_size: int,
) -> dict[str, Any]:
    payload = formal_service._build_trajectory_payload(
        benchmark_agents=context.spawned_agents[:batch_size],
        decision_targets=decision_targets,
        batch_id=batch_id,
        simulation_time_ms=simulation_time_ms,
        progress_ratio=progress_ratio,
    )
    return {
        "method": "POST",
        "url": f"/api/v1/experiments/{context.experiment_id}/trajectories",
        "expect_status": 200,
        "experiment_id": context.experiment_id,
        "json": payload,
        "extra": {"operation": "trajectory", "batch_id": batch_id, "batch_size": batch_size},
        "extra_builder": lambda response, elapsed_ms: formal_service._build_trajectory_ingest_extra(
            response=response,
            elapsed_ms=elapsed_ms,
            batch_id=batch_id,
        ),
    }


def _build_mixed_request_spec(
    *,
    operation: str,
    context: formal_service.BenchmarkExperimentContext,
    decision_targets: dict[str, dict[str, Any]],
    heatmap_payload: dict[str, Any],
    round_index: int,
    ordinal: int,
) -> dict[str, Any]:
    if operation == "decision":
        return _build_decision_request_spec(
            context=context,
            decision_targets=decision_targets,
            batch_id=f"p0_mix_decision_{round_index}_{ordinal}_{uuid4().hex[:6]}",
            simulation_time_ms=900_000 + round_index * 10_000 + ordinal * 100,
        )
    if operation == "trajectory":
        return _build_trajectory_request_spec(
            context=context,
            decision_targets=decision_targets,
            batch_id=f"p0_mix_traj_{round_index}_{ordinal}_{uuid4().hex[:6]}",
            simulation_time_ms=920_000 + round_index * 10_000 + ordinal * 100,
            progress_ratio=0.22 + (ordinal % 3) * 0.04,
            batch_size=2000,
        )
    if operation == "stats":
        return {
            "method": "GET",
            "url": f"/api/v1/experiments/{context.experiment_id}/stats",
            "expect_status": 200,
            "experiment_id": context.experiment_id,
            "extra": {"operation": "stats"},
        }
    if operation == "heatmap":
        return {
            "method": "POST",
            "url": f"/api/v1/experiments/{context.experiment_id}/heatmap",
            "expect_status": 200,
            "experiment_id": context.experiment_id,
            "json": dict(heatmap_payload),
            "extra": {"operation": "heatmap"},
        }
    raise ValueError(f"未知混合流量 operation: {operation}")


def _build_heatmap_payload() -> dict[str, Any]:
    return {
        "time_start_ms": 10_000,
        "time_end_ms": 69_000,
        "grid_config": {
            "origin": {"x": -10.0, "z": -10.0},
            "cell_size": 2.0,
            "cols": 100,
            "rows": 60,
        },
        "metric": "passage_count",
        "crowd_type_filter": None,
        "zone_id_filter": None,
        "normalization": "min_max",
    }


def _drain_maintenance_jobs() -> dict[str, Any]:
    settings = get_settings()
    started = perf_counter()
    if settings.redis_enabled:
        result = run_maintenance_rq_worker(
            burst=True,
            max_jobs=16,
            with_scheduler=True,
            worker_name=f"p0-benchmark-{uuid4().hex[:6]}",
        )
        return {
            "worker_backend": "rq",
            "duration_ms": round((perf_counter() - started) * 1000, 2),
            "result": result,
        }

    result = asyncio.run(
        run_maintenance_worker(
            limit=16,
            poll_interval_seconds=0.05,
            max_cycles=40,
            stop_when_idle=True,
        )
    )
    return {
        "worker_backend": "local",
        "duration_ms": round((perf_counter() - started) * 1000, 2),
        "result": result.as_dict(),
    }


def _count_maintenance_jobs_by_status(experiment_id: str) -> dict[str, int]:
    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
        if experiment is None:
            return {}
        jobs = session.scalars(
            select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
        ).all()
        counts = {status.value: 0 for status in MaintenanceJobStatus}
        for job in jobs:
            counts[str(job.status.value)] += 1
        return {key: value for key, value in counts.items() if value > 0}
    finally:
        session.close()


def _override_allocation_counts(
    *,
    client: RealHttpBenchmarkClient,
    context: formal_service.BenchmarkExperimentContext,
    total_agents: int,
    suffix: str,
) -> None:
    type_counts = scale_type_counts(total_agents)
    response = client.put(
        f"/api/v1/experiments/{context.experiment_id}/crowd-allocation",
        json={
            "groups": [
                {
                    "type_id": context.type_ids[type_code],
                    "count": type_counts[type_code],
                    "distribution": {
                        "mode": "zone_fixed",
                        "fixed_zone_id": "zone_lobby",
                        "spread_radius": 10.0,
                        "avoid_exits": True,
                    },
                }
                for type_code, *_ in formal_service.TYPE_BLUEPRINTS
            ],
            "agent_id_prefix": f"p0_{suffix[-6:]}",
        },
    )
    formal_service._assert_success(response, expected_status=200, label="更新压测人群分配")
    context.allocation_counts = {
        context.type_ids[type_code]: type_counts[type_code]
        for type_code, *_ in formal_service.TYPE_BLUEPRINTS
    }


def _calculate_throughput(*, sample_count: int, wall_elapsed_ms: float) -> float:
    if sample_count <= 0 or wall_elapsed_ms <= 0:
        return 0.0
    return round(sample_count / (wall_elapsed_ms / 1000.0), 2)


def _append_existing_sample(
    samples_by_stage: dict[str, list[formal_service.BenchmarkSample]],
    stage: str,
    sample: formal_service.BenchmarkSample,
    *,
    run_index: int,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = dict(sample.extra)
    payload["run"] = run_index + 1
    if extra:
        payload.update(extra)
    samples_by_stage[stage].append(
        formal_service.BenchmarkSample(
            duration_ms=sample.duration_ms,
            status_code=sample.status_code,
            extra=payload,
        )
    )


def _append_duration_sample(
    samples_by_stage: dict[str, list[formal_service.BenchmarkSample]],
    *,
    stage: str,
    duration_ms: float,
    run_index: int,
    extra: dict[str, Any] | None = None,
) -> None:
    payload = dict(extra or {})
    payload["run"] = run_index + 1
    samples_by_stage[stage].append(
        formal_service.BenchmarkSample(
            duration_ms=round(duration_ms, 2),
            status_code=200,
            extra=payload,
        )
    )


def _resolve_suite_output_targets(
    *,
    report_path: str | None,
    output_json_path: str | None,
) -> tuple[Path, Path]:
    backend_root = Path(__file__).resolve().parents[3]
    report_target = (
        Path(report_path)
        if report_path is not None
        else backend_root / "P0压测报告.md"
    )
    json_target = (
        Path(output_json_path)
        if output_json_path is not None
        else backend_root / "logs" / "benchmarks" / "p0-benchmark-latest.json"
    )
    report_target.parent.mkdir(parents=True, exist_ok=True)
    json_target.parent.mkdir(parents=True, exist_ok=True)
    return report_target, json_target


def _cleanup_benchmark_artifacts(*, experiment_id: str, type_ids: list[str]) -> None:
    session = get_session_factory()()
    experiment_pk: int | None = None
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
        experiment_pk = experiment.id if experiment is not None else None

        if experiment is not None:
            session.execute(delete(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id))
            session.execute(delete(AgentMetric1m).where(AgentMetric1m.experiment_id == experiment.id))
            session.execute(delete(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id))
            session.execute(delete(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id))
            session.execute(delete(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment.id))
            session.execute(
                delete(TrajectoryPointSampled1s).where(TrajectoryPointSampled1s.experiment_id == experiment.id)
            )
            session.execute(delete(TrajectoryPoint).where(TrajectoryPoint.experiment_id == experiment.id))
            session.execute(delete(TrajectoryBatch).where(TrajectoryBatch.experiment_id == experiment.id))
            session.execute(delete(AgentDecision).where(AgentDecision.experiment_id == experiment.id))
            session.execute(delete(DecisionBatch).where(DecisionBatch.experiment_id == experiment.id))
            session.execute(delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id))
            session.execute(delete(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment.id))
            session.execute(delete(ExperimentAllocation).where(ExperimentAllocation.experiment_id == experiment.id))
            session.execute(delete(ExperimentExit).where(ExperimentExit.experiment_id == experiment.id))
            session.execute(delete(ExperimentZone).where(ExperimentZone.experiment_id == experiment.id))
            session.execute(delete(Experiment).where(Experiment.id == experiment.id))

        if type_ids:
            session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id.in_(type_ids)))

        session.commit()
    finally:
        session.close()

    redis_client = get_redis_client()
    if experiment_pk is not None:
        redis_client.delete_by_pattern("heatmap", "response", "exp", str(experiment_pk), "*")
        redis_client.delete_by_pattern("spawn-positions", "response", "exp", str(experiment_pk), "*")


def _assert_http_server_ready(base_url: str, *, timeout_seconds: float) -> None:
    client = RealHttpBenchmarkClient(base_url, timeout_seconds=timeout_seconds)
    try:
        healthz = client.get("/healthz")
        if healthz.status_code != 200:
            raise RuntimeError(f"HTTP 服务健康检查失败: status={healthz.status_code} body={healthz.text}")
        db_health = client.get("/healthz/db")
        if db_health.status_code != 200:
            raise RuntimeError(f"HTTP 服务数据库健康检查失败: status={db_health.status_code} body={db_health.text}")
    finally:
        client.close()


def _benchmark_suffix(prefix: str) -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%d%H%M%S')}_{uuid4().hex[:6]}"


def _build_suite_summary(results: dict[str, Any]) -> dict[str, Any]:
    scenarios: list[dict[str, Any]] = list(results["scenarios"])
    total_metrics = 0
    threshold_metric_count = 0
    passed_metric_count = 0
    scenario_summaries: list[dict[str, Any]] = []
    for scenario in scenarios:
        metrics = list(scenario["metrics"])
        total_metrics += len(metrics)
        scenario_threshold_count = sum(1 for metric in metrics if metric.get("threshold") is not None)
        scenario_passed_count = sum(1 for metric in metrics if metric.get("status") == "通过")
        threshold_metric_count += scenario_threshold_count
        passed_metric_count += scenario_passed_count
        scenario_summaries.append(
            {
                "key": scenario["key"],
                "display_name": scenario["display_name"],
                "metric_count": len(metrics),
                "threshold_metric_count": scenario_threshold_count,
                "passed_metric_count": scenario_passed_count,
            }
        )
    return {
        "scenario_count": len(scenarios),
        "metric_count": total_metrics,
        "threshold_metric_count": threshold_metric_count,
        "passed_metric_count": passed_metric_count,
        "scenario_summaries": scenario_summaries,
    }


def _build_suite_report(results: dict[str, Any]) -> str:
    lines = [
        "# P0压测报告",
        "",
        f"- 生成时间：`{results['generated_at']}`",
        f"- HTTP 基地址：`{results['http_base_url']}`",
        f"- 场景数量：`{results['summary']['scenario_count']}`",
        f"- 阈值通过情况：`{results['summary']['passed_metric_count']}/{results['summary']['threshold_metric_count']}`",
        "",
        "## 环境信息",
        "",
        f"- Python：`{results['environment']['python_version']}`",
        f"- 平台：`{results['environment']['platform']}`",
        f"- PostgreSQL：`{results['environment']['server_version']}`",
        f"- TimescaleDB：`{results['environment']['timescaledb_version']}`",
        f"- Redis：`{'启用' if results['environment']['redis_enabled'] else '未启用'}`",
        f"- 决策引擎模式：`{results['environment']['decision_engine_mode']}`",
        f"- 并发档位：`{', '.join(str(value) for value in results['environment']['concurrency_levels'])}`",
        f"- 混合并发：`{results['environment']['mixed_concurrency']}`",
        "",
        "## 场景摘要",
        "",
        "| 场景 | 指标数 | 阈值指标数 | 已通过 |",
        "| --- | ---: | ---: | ---: |",
    ]
    for summary in results["summary"]["scenario_summaries"]:
        lines.append(
            "| {display_name} | {metric_count} | {threshold_metric_count} | {passed_metric_count} |".format(
                **summary
            )
        )

    for scenario in results["scenarios"]:
        metrics = list(scenario["metrics"])
        lines.extend(
            [
                "",
                f"## {scenario['display_name']}",
                "",
                f"- 描述：{scenario['description']}",
                f"- 传输口径：`{scenario['transport']}`",
            ]
        )
        metadata = dict(scenario.get("metadata") or {})
        if metadata:
            for key, value in metadata.items():
                rendered = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                lines.append(f"- {key}：`{rendered}`")
        lines.extend(
            [
                "",
                "| 指标 | 平均耗时(ms) | P95(ms) | P99(ms) | 最大耗时(ms) | 平均吞吐 | 阈值对照 | 结论 |",
                "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
            ]
        )
        for metric in metrics:
            avg_throughput = metric.get("avg_throughput_rps")
            throughput_text = "-" if avg_throughput is None else f"{avg_throughput:.2f}/s"
            lines.append(
                "| {display_name} | {avg_ms:.2f} | {p95_ms:.2f} | {p99_ms:.2f} | {max_ms:.2f} | {throughput} | {threshold_summary} | {status} |".format(
                    throughput=throughput_text,
                    **metric,
                )
            )
        for metric in metrics:
            notes = list(metric.get("notes") or [])
            if not notes:
                continue
            lines.append("")
            lines.append(f"### {metric['display_name']}")
            lines.append("")
            for note in notes:
                lines.append(f"- {note}")

    return "\n".join(lines).strip() + "\n"


def _import_httpx():
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - 运行时错误提示
        raise RuntimeError(
            "运行 P0 真实 HTTP 压测需要安装 httpx。请在 backend 目录执行 `python -m pip install -e \".[dev]\"`。"
        ) from exc
    return httpx
