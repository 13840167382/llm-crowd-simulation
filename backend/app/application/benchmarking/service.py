"""正式压测服务。

用于本地快速构造一套较接近 PDF 9.4 的压测数据，并直接对 HTTP 接口做基线压测。
这里使用 FastAPI TestClient、当前数据库和当前 Redis，是“单机应用层压测”，主要验证
后端接口、批量写入、聚合查询和缓存路径的性能基线；它不是线上流量压测，也不覆盖网关、
负载均衡、多进程部署或 Unity 客户端网络开销。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import json
import logging
from math import ceil, floor
from pathlib import Path
import platform
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, insert, select, text
from sqlalchemy.orm import Session

from backend.app.application.analytics.aggregation import (
    refresh_agent_metrics_for_times,
    refresh_decision_metrics_for_times,
    refresh_trajectory_metrics_for_times,
    refresh_trajectory_samples_for_agents,
)
from backend.app.core.config import get_settings
from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import AgentState, DecisionSource
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    DecisionBatch,
    Experiment,
    ExperimentAgent,
    HeatmapCache,
    TrajectoryBatch,
    TrajectoryPoint,
    TrajectoryPointSampled1s,
)
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.main import create_app

logger = get_logger(__name__)

PDF_THRESHOLDS: dict[str, dict[str, Any]] = {
    "crowd_types_list": {"type": "latency_ms", "target": 100, "label": "人群模板列表查询 ≤ 100 ms"},
    "spawn_positions_2000": {"type": "latency_ms", "target": 3000, "label": "生成位置（2000 人） ≤ 3 秒"},
    "decision_batch_100": {
        "type": "latency_ms_p99",
        "target": 6000,
        "label": "批量决策接口 P99 ≤ 6 秒（100 人批次）",
    },
    "decision_batch_hard_timeout": {"type": "latency_ms_max", "target": 8000, "label": "批量决策硬超时 ≤ 8 秒"},
    "trajectory_ingest_2000": {"type": "latency_ms", "target": 500, "label": "轨迹上报写入延迟 ≤ 500 ms"},
    "trajectory_ingest_throughput": {
        "type": "throughput_rps",
        "target": 2000,
        "label": "轨迹上报吞吐 ≥ 2000 条/秒",
    },
    "stats": {"type": "latency_ms", "target": 2000, "label": "统计概览查询 ≤ 2 秒"},
    "single_agent_trajectory": {"type": "latency_ms", "target": 1000, "label": "单人物轨迹查询 ≤ 1 秒"},
    "path_traces_100": {"type": "latency_ms", "target": 1500, "label": "路径追踪查询（100 人） ≤ 1.5 秒"},
    "heatmap_6000": {"type": "latency_ms", "target": 5000, "label": "热力图生成（6000 格） ≤ 5 秒"},
    "heatmap_compare_3": {"type": "latency_ms", "target": 12000, "label": "分人群热力图对比（3 类） ≤ 12 秒"},
}

TYPE_BLUEPRINTS = (
    ("tourist", "观光游客", "#FF6B35", "zone_exhibit", "exit_lobby_to_exhibit"),
    ("shopper", "购物游客", "#2A9D8F", "zone_commercial", "exit_lobby_to_commercial"),
    ("resident", "常驻居民", "#457B9D", "zone_rest", "exit_lobby_to_rest"),
)
TYPE_COUNTS = {"tourist": 700, "shopper": 700, "resident": 600}
ZONE_DISPLAY_NAMES = {
    "zone_lobby": "主大厅",
    "zone_exhibit": "展览区",
    "zone_rest": "休闲区",
    "zone_commercial": "商业区",
}
DECISION_SCENARIOS = {
    "tourist": (
        {
            "preferred_zone_id": "zone_exhibit",
            "summary_template": "当前位于主大厅中部，刚看完导览屏，希望优先前往展览区参加热门展项，但不想在入口处排太久。",
            "intent_label": "继续参观核心展项",
            "urgency_bias": 0.08,
        },
        {
            "preferred_zone_id": "zone_rest",
            "summary_template": "已经连续步行了一段时间，想先去休闲区短暂停留补充体力，再决定后续参观路线。",
            "intent_label": "短暂休整后继续参观",
            "urgency_bias": -0.04,
        },
        {
            "preferred_zone_id": "zone_commercial",
            "summary_template": "准备先去商业区购买纪念品，之后再返回展区，希望沿途人流不要太拥挤。",
            "intent_label": "顺路购买纪念品",
            "urgency_bias": 0.02,
        },
    ),
    "shopper": (
        {
            "preferred_zone_id": "zone_commercial",
            "summary_template": "刚收到限时促销提醒，想尽快前往商业区，但也会考虑当前通道的排队长度。",
            "intent_label": "赶往限时活动店铺",
            "urgency_bias": 0.10,
        },
        {
            "preferred_zone_id": "zone_exhibit",
            "summary_template": "同行人暂时想去展览区集合，自己需要在商业区和展览区之间权衡绕行成本。",
            "intent_label": "先与同行人会合",
            "urgency_bias": 0.01,
        },
        {
            "preferred_zone_id": "zone_rest",
            "summary_template": "手上购物袋较多，想先到休闲区整理物品，避免继续在拥挤区域停留。",
            "intent_label": "整理已购物品",
            "urgency_bias": -0.03,
        },
    ),
    "resident": (
        {
            "preferred_zone_id": "zone_rest",
            "summary_template": "准备回到熟悉的休闲区处理日常事务，希望路线更稳定、等待更少。",
            "intent_label": "回到常驻活动区域",
            "urgency_bias": 0.03,
        },
        {
            "preferred_zone_id": "zone_commercial",
            "summary_template": "需要先去商业区取一件预订物品，因此会优先考虑通行效率更高的出口。",
            "intent_label": "取预订物品",
            "urgency_bias": 0.07,
        },
        {
            "preferred_zone_id": "zone_exhibit",
            "summary_template": "计划临时去展览区接人，虽然不是常用路线，但可以接受中等程度的绕行。",
            "intent_label": "临时前往展区接人",
            "urgency_bias": -0.01,
        },
    ),
}


@dataclass(slots=True)
class BenchmarkSample:
    """单次压测采样结果。"""

    duration_ms: float
    status_code: int
    extra: dict[str, Any]


@dataclass(slots=True)
class BenchmarkMetric:
    """单项压测指标汇总。"""

    key: str
    display_name: str
    unit: str
    warmup_runs: int
    measured_runs: int
    samples: list[BenchmarkSample]
    threshold: dict[str, Any] | None = None
    notes: list[str] | None = None

    def durations_ms(self) -> list[float]:
        return [sample.duration_ms for sample in self.samples]

    def avg_ms(self) -> float:
        values = self.durations_ms()
        return round(sum(values) / len(values), 2) if values else 0.0

    def p50_ms(self) -> float:
        return _percentile(self.durations_ms(), 0.50)

    def p95_ms(self) -> float:
        return _percentile(self.durations_ms(), 0.95)

    def p99_ms(self) -> float:
        return _percentile(self.durations_ms(), 0.99)

    def max_ms(self) -> float:
        values = self.durations_ms()
        return round(max(values), 2) if values else 0.0

    def avg_throughput(self) -> float | None:
        values = [float(sample.extra["throughput_rps"]) for sample in self.samples if "throughput_rps" in sample.extra]
        if not values:
            return None
        return round(sum(values) / len(values), 2)

    def min_throughput(self) -> float | None:
        values = [float(sample.extra["throughput_rps"]) for sample in self.samples if "throughput_rps" in sample.extra]
        if not values:
            return None
        return round(min(values), 2)

    def threshold_result(self) -> tuple[str, str]:
        """把阈值配置转换为报告中的可读结果。

        阈值类型分为延迟类和吞吐类：延迟比较 P95/P99/MAX 是否小于目标，
        吞吐比较平均 RPS 是否达到目标。
        """

        if self.threshold is None:
            return "未设阈值", "无"

        threshold_type = str(self.threshold["type"])
        target = float(self.threshold["target"])
        if threshold_type == "latency_ms":
            actual = self.p95_ms()
            passed = actual <= target
            return f"P95={actual:.2f} ms / 阈值≤{target:.0f} ms", "通过" if passed else "未通过"
        if threshold_type == "latency_ms_p99":
            actual = self.p99_ms()
            passed = actual <= target
            return f"P99={actual:.2f} ms / 阈值≤{target:.0f} ms", "通过" if passed else "未通过"
        if threshold_type == "latency_ms_max":
            actual = self.max_ms()
            passed = actual <= target
            return f"MAX={actual:.2f} ms / 阈值≤{target:.0f} ms", "通过" if passed else "未通过"
        if threshold_type == "throughput_rps":
            actual = self.avg_throughput() or 0.0
            passed = actual >= target
            return f"AVG={actual:.2f} 点/秒 / 阈值≥{target:.0f}", "通过" if passed else "未通过"
        return "未知阈值", "未知"

    def to_dict(self) -> dict[str, Any]:
        threshold_summary, status = self.threshold_result()
        return {
            "key": self.key,
            "display_name": self.display_name,
            "unit": self.unit,
            "warmup_runs": self.warmup_runs,
            "measured_runs": self.measured_runs,
            "durations_ms": [round(value, 2) for value in self.durations_ms()],
            "avg_ms": self.avg_ms(),
            "p50_ms": self.p50_ms(),
            "p95_ms": self.p95_ms(),
            "p99_ms": self.p99_ms(),
            "max_ms": self.max_ms(),
            "avg_throughput_rps": self.avg_throughput(),
            "min_throughput_rps": self.min_throughput(),
            "threshold": self.threshold,
            "threshold_summary": threshold_summary,
            "status": status,
            "sample_extras": [sample.extra for sample in self.samples],
            "notes": list(self.notes or []),
        }


@dataclass(slots=True)
class BenchmarkExperimentContext:
    """压测实验上下文。"""

    experiment_id: str
    type_ids: dict[str, str]
    spawned_agents: list[dict[str, Any]]
    allocation_counts: dict[str, int]


class BenchmarkApiClient:
    """带统一请求头的压测客户端。"""

    def __init__(self) -> None:
        self.client = TestClient(create_app())
        self.experiment_id: str | None = None

    def set_experiment(self, experiment_id: str) -> None:
        self.experiment_id = experiment_id

    def request(self, method: str, url: str, **kwargs):
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.setdefault("X-Request-Id", f"benchmark-{uuid4().hex[:12]}")
        if url.startswith("/api/v1/experiments/") and self.experiment_id is not None:
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


def run_formal_benchmark(
    *,
    report_path: str | None = None,
    output_json_path: str | None = None,
) -> dict[str, Any]:
    """运行正式压测并输出 Markdown 报告与 JSON 结果。

    完整流程是：采集环境信息、构造独立实验与人群模板、生成出生点、启动实验、
    压测决策和轨迹上报、灌入历史运行数据、刷新聚合、压测分析查询，最后写出
    Markdown 报告和 JSON 机器可读结果。函数会临时降低部分 noisy logger 的级别，
    避免大量 SQL/HTTP 日志干扰压测输出。
    """

    settings = get_settings()
    timestamp = datetime.now(UTC)
    report_target = (
        Path(report_path)
        if report_path is not None
        else Path(__file__).resolve().parents[3] / "正式压测报告.md"
    )
    json_target = (
        Path(output_json_path)
        if output_json_path is not None
        else Path(__file__).resolve().parents[3] / "logs" / "benchmarks" / "formal-benchmark-latest.json"
    )
    json_target.parent.mkdir(parents=True, exist_ok=True)
    report_target.parent.mkdir(parents=True, exist_ok=True)

    suffix = timestamp.strftime("%Y%m%d%H%M%S")
    client = BenchmarkApiClient()
    sqlalchemy_logger = logging.getLogger("sqlalchemy.engine")
    sqlalchemy_engine_logger = logging.getLogger("sqlalchemy.engine.Engine")
    original_sqlalchemy_level = sqlalchemy_logger.level
    original_sqlalchemy_engine_level = sqlalchemy_engine_logger.level
    sqlalchemy_logger.setLevel(logging.WARNING)
    sqlalchemy_engine_logger.setLevel(logging.WARNING)
    muted_loggers = {
        "backend.app.main": logging.WARNING,
        "backend.app.application.decisions.service": logging.WARNING,
        "backend.app.infrastructure.cache.redis_client": logging.WARNING,
        "httpx": logging.WARNING,
        "asyncio": logging.WARNING,
    }
    original_muted_levels = {name: logging.getLogger(name).level for name in muted_loggers}
    for name, level in muted_loggers.items():
        logging.getLogger(name).setLevel(level)

    try:
        environment = _collect_environment(settings=settings)
        context = _prepare_benchmark_experiment(client=client, suffix=suffix)
        spawn_metric = _benchmark_spawn_positions(client=client, context=context)
        _finalize_spawn_and_start_experiment(client=client, context=context)

        decision_metric = _benchmark_decision_batches(client=client, context=context)
        trajectory_ingest_metric = _benchmark_trajectory_ingest(client=client, context=context)

        _seed_runtime_history(context=context)
        _refresh_analytics(context=context)

        analytics_metrics = _benchmark_analytics_endpoints(client=client, context=context)

        metrics = [spawn_metric, decision_metric, trajectory_ingest_metric, *analytics_metrics]
        crowd_types_metric = _benchmark_crowd_types_list(client=client)
        metrics.insert(0, crowd_types_metric)

        results = {
            "generated_at": timestamp.isoformat(),
            "environment": environment,
            "dataset": _build_dataset_summary(context=context),
            "metrics": [metric.to_dict() for metric in metrics],
        }
        json_target.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
        report_target.write_text(_build_report(results), encoding="utf-8")
        logger.info(
            "正式压测完成: experiment_id=%s report_path=%s json_path=%s",
            context.experiment_id,
            report_target,
            json_target,
        )
        return {
            "generated_at": results["generated_at"],
            "experiment_id": context.experiment_id,
            "report_path": str(report_target),
            "json_path": str(json_target),
            "dataset": results["dataset"],
            "metrics": results["metrics"],
        }
    finally:
        sqlalchemy_logger.setLevel(original_sqlalchemy_level)
        sqlalchemy_engine_logger.setLevel(original_sqlalchemy_engine_level)
        for name, level in original_muted_levels.items():
            logging.getLogger(name).setLevel(level)
        client.close()


def _collect_environment(*, settings) -> dict[str, Any]:
    """采集压测报告需要展示的运行环境和关键配置。"""

    session = get_session_factory()()
    try:
        server_version = session.execute(text("SELECT version()")).scalar_one()
        timescaledb_version = session.execute(
            text("SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
        ).scalar_one_or_none()
    finally:
        session.close()

    return {
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "environment": settings.environment,
        "db_echo": settings.db_echo,
        "redis_enabled": settings.redis_enabled,
        "decision_engine_mode": settings.decision_engine_mode,
        "decision_batch_max_concurrency": settings.decision_batch_max_concurrency,
        "decision_batch_hard_timeout_seconds": settings.decision_batch_hard_timeout_seconds,
        "trajectory_query_sample_interval_ms": settings.trajectory_query_sample_interval_ms,
        "trajectory_query_max_raw_window_ms": settings.trajectory_query_max_raw_window_ms,
        "trajectory_query_max_raw_page_size": settings.trajectory_query_max_raw_page_size,
        "server_version": str(server_version),
        "timescaledb_version": timescaledb_version,
        "redis_key_prefix": settings.redis_key_prefix if settings.redis_enabled else None,
        "methodology": "基于 FastAPI TestClient + 本地 PostgreSQL/TimescaleDB/Redis 的单机应用层压测",
    }


def _prepare_benchmark_experiment(*, client: BenchmarkApiClient, suffix: str) -> BenchmarkExperimentContext:
    """创建本次压测专用的人群模板、实验、拓扑和人群分配。

    所有业务 ID 都带 timestamp suffix，避免和已有实验或上一次压测数据冲突。
    """

    type_ids: dict[str, str] = {}
    short_code_map = {"tourist": "bt", "shopper": "bs", "resident": "br"}
    for type_code, display_name, color_hex, _, _ in TYPE_BLUEPRINTS:
        type_id = f"{short_code_map[type_code]}_{suffix}"
        type_ids[type_code] = type_id
        response = client.post(
            "/api/v1/crowd-types",
            json={
                "type_id": type_id,
                "display_name": display_name,
                "color_hex": color_hex,
                "base_speed": 1.18,
                "social_distance": 0.8,
                "tolerance_duration_ms": 24000,
                "attraction_weights": {
                    "exhibit": 0.9 if type_code == "tourist" else 0.3,
                    "commercial": 0.9 if type_code == "shopper" else 0.4,
                    "rest": 0.9 if type_code == "resident" else 0.2,
                    "service": 0.3,
                    "plaza": 0.5,
                    "passage": 0.6,
                    "exit_final": 0.1,
                },
                "metadata": {"benchmark": True, "type_code": type_code},
            },
        )
        _assert_success(response, expected_status=201, label=f"创建模板 {type_id}")

    experiment_response = client.post(
        "/api/v1/experiments",
        json={
            "name": f"正式压测实验-{suffix}",
            "description": "用于验证 PDF 9.4 指标的正式压测数据集",
            "map_id": "benchmark-map",
            "llm_config": {
                "model": "benchmark-decision-model",
                "temperature": 0.0,
                "max_tokens": 256,
                "timeout_ms": 600000,
            },
            "decision_config": {
                "batch_interval_ms": 5000,
                "batch_size": 1000,
                "fallback_to_algorithm": True,
            },
            "tags": ["benchmark", "formal"],
        },
    )
    _assert_success(experiment_response, expected_status=201, label="创建压测实验")
    experiment_id = str(experiment_response.json()["data"]["experiment_id"])
    client.set_experiment(experiment_id)

    topology_response = client.put(
        f"/api/v1/experiments/{experiment_id}/topology",
        json={
            "zones": [
                {
                    "zone_id": "zone_lobby",
                    "zone_name": "主大厅",
                    "zone_type": "plaza",
                    "bounds": {"min_x": 0, "min_z": 0, "max_x": 60, "max_z": 60},
                    "capacity": 2400,
                    "is_entry_zone": True,
                    "metadata": {"benchmark": True},
                },
                {
                    "zone_id": "zone_exhibit",
                    "zone_name": "展览区",
                    "zone_type": "exhibit",
                    "bounds": {"min_x": 80, "min_z": 0, "max_x": 140, "max_z": 60},
                    "capacity": 1600,
                    "is_entry_zone": False,
                    "metadata": {"benchmark": True},
                },
                {
                    "zone_id": "zone_rest",
                    "zone_name": "休闲区",
                    "zone_type": "rest",
                    "bounds": {"min_x": 0, "min_z": 80, "max_x": 60, "max_z": 140},
                    "capacity": 1200,
                    "is_entry_zone": False,
                    "metadata": {"benchmark": True},
                },
                {
                    "zone_id": "zone_commercial",
                    "zone_name": "商业区",
                    "zone_type": "commercial",
                    "bounds": {"min_x": 80, "min_z": 80, "max_x": 140, "max_z": 140},
                    "capacity": 1500,
                    "is_entry_zone": False,
                    "metadata": {"benchmark": True},
                },
            ],
            "exits": [
                {
                    "exit_id": "exit_lobby_to_exhibit",
                    "source_zone_id": "zone_lobby",
                    "target_zone_id": "zone_exhibit",
                    "exit_type": "portal",
                    "position": {"x": 70, "z": 20},
                    "pass_capacity_per_sec": 35,
                    "metadata": {"benchmark": True},
                },
                {
                    "exit_id": "exit_lobby_to_rest",
                    "source_zone_id": "zone_lobby",
                    "target_zone_id": "zone_rest",
                    "exit_type": "portal",
                    "position": {"x": 20, "z": 70},
                    "pass_capacity_per_sec": 30,
                    "metadata": {"benchmark": True},
                },
                {
                    "exit_id": "exit_lobby_to_commercial",
                    "source_zone_id": "zone_lobby",
                    "target_zone_id": "zone_commercial",
                    "exit_type": "portal",
                    "position": {"x": 75, "z": 75},
                    "pass_capacity_per_sec": 32,
                    "metadata": {"benchmark": True},
                },
            ],
        },
    )
    _assert_success(topology_response, expected_status=200, label="提交压测拓扑")

    allocation_response = client.put(
        f"/api/v1/experiments/{experiment_id}/crowd-allocation",
        json={
            "groups": [
                {
                    "type_id": type_ids[type_code],
                    "count": TYPE_COUNTS[type_code],
                    "distribution": {
                        "mode": "zone_fixed",
                        "fixed_zone_id": "zone_lobby",
                        "spread_radius": 10.0,
                        "avoid_exits": True,
                    },
                }
                for type_code, *_ in TYPE_BLUEPRINTS
            ],
            "agent_id_prefix": f"bench_{suffix[-6:]}",
        },
    )
    _assert_success(allocation_response, expected_status=200, label="提交压测分配")

    return BenchmarkExperimentContext(
        experiment_id=experiment_id,
        type_ids=type_ids,
        spawned_agents=[],
        allocation_counts={type_ids[type_code]: TYPE_COUNTS[type_code] for type_code, *_ in TYPE_BLUEPRINTS},
    )


def _benchmark_spawn_positions(*, client: BenchmarkApiClient, context: BenchmarkExperimentContext) -> BenchmarkMetric:
    """测量 2000 人出生点生成接口。

    使用不同 seed 做多次测量，避免同一个 seed 命中幂等缓存导致结果偏乐观。
    最后把最新一批 agents 放回 context，供启动实验和后续决策/轨迹压测使用。
    """

    seeds = [101, 202, 303]
    warmup_seed = 77
    _measure_http_call(
        operation=lambda: client.post(
            f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
            json={"seed": warmup_seed},
        ),
        expect_status=200,
    )

    samples: list[BenchmarkSample] = []
    latest_agents: list[dict[str, Any]] = []
    for seed in seeds:
        sample = _measure_http_call(
            operation=lambda seed_value=seed: client.post(
                f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
                json={"seed": seed_value},
            ),
            expect_status=200,
            extra_builder=lambda response, elapsed_ms, seed_value=seed: {
                "seed": seed_value,
                "generated_count": int(response.json()["data"]["total_count"]),
            },
        )
        if "_agents" in sample.extra:
            latest_agents = _normalize_spawned_agents(sample.extra.pop("_agents"))
        samples.append(sample)

    if not latest_agents:
        final_response = client.post(
            f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
            json={"seed": 404},
        )
        _assert_success(final_response, expected_status=200, label="获取最终出生点")
        latest_agents = _normalize_spawned_agents(final_response.json()["data"]["agents"])
    context.spawned_agents = latest_agents

    return BenchmarkMetric(
        key="spawn_positions_2000",
        display_name="生成位置（2000 人）",
        unit="ms",
        warmup_runs=1,
        measured_runs=len(samples),
        samples=samples,
        threshold=PDF_THRESHOLDS["spawn_positions_2000"],
        notes=["同一实验下使用不同 seed 重复测量，以避免幂等缓存命中影响结果。"],
    )


def _finalize_spawn_and_start_experiment(*, client: BenchmarkApiClient, context: BenchmarkExperimentContext) -> None:
    """确保已有出生点并启动压测实验，使运行期接口进入可写状态。"""

    if not context.spawned_agents:
        response = client.post(
            f"/api/v1/experiments/{context.experiment_id}/spawn-positions",
            json={"seed": 505},
        )
        _assert_success(response, expected_status=200, label="补充出生点")
        context.spawned_agents = _normalize_spawned_agents(response.json()["data"]["agents"])

    start_response = client.post(
        f"/api/v1/experiments/{context.experiment_id}/start",
        json={"actual_start_time_ms": 1700000000000},
    )
    _assert_success(start_response, expected_status=200, label="启动压测实验")


def _benchmark_decision_batches(*, client: BenchmarkApiClient, context: BenchmarkExperimentContext) -> BenchmarkMetric:
    """测量批量决策接口。

    这里只取前 256 个压测人物构造重复批次，重点观察后端批量调度、并发控制、
    LLM/fallback 结果落库和聚合刷新耗时。不同 batch_id 用来绕开幂等缓存。
    """

    benchmark_agents = context.spawned_agents[:256]
    decision_targets = _build_agent_target_map(context)
    settings = get_settings()
    decision_engine_mode = settings.decision_engine_mode
    if decision_engine_mode in {"heuristic", "mock", "local"}:
        engine_note = "当前环境使用的是 heuristic 决策引擎模式，用于验证后端批量调度与并发控制性能。"
    else:
        engine_note = f"当前环境使用的是 {decision_engine_mode} 决策引擎模式，指标会包含外部模型服务调用耗时。"

    warmup_payload = _build_decision_request_payload(
        context=context,
        benchmark_agents=benchmark_agents,
        decision_targets=decision_targets,
        batch_id=f"benchmark_decision_warmup_{uuid4().hex[:6]}",
        simulation_time_ms=300_000,
    )
    _measure_http_call(
        operation=lambda: client.post(
            f"/api/v1/experiments/{context.experiment_id}/decisions",
            json=warmup_payload,
        ),
        expect_status=200,
    )

    samples: list[BenchmarkSample] = []
    for index in range(10):
        batch_id = f"benchmark_decision_batch_{index}_{uuid4().hex[:6]}"
        simulation_time_ms = 305_000 + index * 5_000
        payload = _build_decision_request_payload(
            context=context,
            benchmark_agents=benchmark_agents,
            decision_targets=decision_targets,
            batch_id=batch_id,
            simulation_time_ms=simulation_time_ms,
        )
        sample = _measure_http_call(
            operation=lambda payload_value=payload: client.post(
                f"/api/v1/experiments/{context.experiment_id}/decisions",
                json=payload_value,
            ),
            expect_status=200,
            extra_builder=lambda response, elapsed_ms, batch_value=batch_id: {
                "batch_id": batch_value,
                "total_count": int(response.json()["data"]["total_count"]),
                "success_count": int(response.json()["data"]["success_count"]),
                "fallback_count": int(response.json()["data"]["fallback_count"]),
            },
        )
        samples.append(sample)

    return BenchmarkMetric(
        key="decision_batch_100",
        display_name="批量决策（100 人批次）",
        unit="ms",
        warmup_runs=1,
        measured_runs=len(samples),
        samples=samples,
        threshold=PDF_THRESHOLDS["decision_batch_100"],
        notes=[
            engine_note,
            f"当前并发上限为 {settings.decision_batch_max_concurrency}。",
            f"整批最大耗时额外对照 PDF 硬超时 {PDF_THRESHOLDS['decision_batch_hard_timeout']['target']} ms。",
        ],
    )


def _benchmark_trajectory_ingest(*, client: BenchmarkApiClient, context: BenchmarkExperimentContext) -> BenchmarkMetric:
    """测量轨迹上报接口的批量写入吞吐。

    每次构造 10 个 agent 的 2000 个轨迹点，额外记录吞吐量，便于和 PDF 中的
    “2000 条/秒”目标直接对照。
    """

    benchmark_agents = context.spawned_agents[:10]
    decision_targets = _build_agent_target_map(context)

    warmup_payload = _build_trajectory_payload(
        benchmark_agents=benchmark_agents,
        decision_targets=decision_targets,
        batch_id=f"benchmark_traj_warmup_{uuid4().hex[:6]}",
        simulation_time_ms=400_000,
        progress_ratio=0.15,
    )
    _measure_http_call(
        operation=lambda: client.post(
            f"/api/v1/experiments/{context.experiment_id}/trajectories",
            json=warmup_payload,
        ),
        expect_status=200,
    )

    samples: list[BenchmarkSample] = []
    for index in range(5):
        batch_id = f"benchmark_traj_batch_{index}_{uuid4().hex[:6]}"
        simulation_time_ms = 401_000 + index * 1_000
        payload = _build_trajectory_payload(
            benchmark_agents=benchmark_agents,
            decision_targets=decision_targets,
            batch_id=batch_id,
            simulation_time_ms=simulation_time_ms,
            progress_ratio=0.20 + index * 0.03,
        )
        sample = _measure_http_call(
            operation=lambda payload_value=payload: client.post(
                f"/api/v1/experiments/{context.experiment_id}/trajectories",
                json=payload_value,
            ),
            expect_status=200,
            extra_builder=lambda response, elapsed_ms, batch_value=batch_id: _build_trajectory_ingest_extra(
                response=response,
                elapsed_ms=elapsed_ms,
                batch_id=batch_value,
            ),
        )
        samples.append(sample)

    return BenchmarkMetric(
        key="trajectory_ingest_2000",
        display_name="轨迹上报（2000 点批次）",
        unit="ms",
        warmup_runs=1,
        measured_runs=len(samples),
        samples=samples,
        threshold=PDF_THRESHOLDS["trajectory_ingest_2000"],
        notes=["结果额外统计平均吞吐，直接对照 PDF 的 2000 条/秒目标。"],
    )


def _seed_runtime_history(*, context: BenchmarkExperimentContext) -> None:
    """直接向数据库灌入较大规模历史决策和轨迹数据。

    HTTP 压测只生成少量运行数据，不足以覆盖统计、热力图和路径追踪查询。
    因此这里绕过 API 写入一批可控历史数据，再交给聚合刷新任务建立查询基线。
    """

    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == context.experiment_id))
        if experiment is None:
            raise RuntimeError(f"压测实验不存在: {context.experiment_id}")

        agent_rows = session.scalars(
            select(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment.id).order_by(ExperimentAgent.spawn_order)
        ).all()
        if not agent_rows:
            raise RuntimeError("压测实验未生成出生点，无法灌入历史轨迹数据。")

        decision_targets = _build_agent_target_map(
            BenchmarkExperimentContext(
                experiment_id=context.experiment_id,
                type_ids=context.type_ids,
                spawned_agents=[
                    {
                        "agent_id": row.agent_id,
                        "crowd_type": row.crowd_type,
                        "spawn_zone_id": row.spawn_zone_id,
                        "spawn_x": float(row.spawn_x),
                        "spawn_z": float(row.spawn_z),
                    }
                    for row in agent_rows
                ],
                allocation_counts=context.allocation_counts,
            )
        )
        _seed_decision_history(session=session, experiment_pk=experiment.id, agent_rows=agent_rows, decision_targets=decision_targets)
        _seed_trajectory_history(session=session, experiment_pk=experiment.id, agent_rows=agent_rows, decision_targets=decision_targets)
        session.commit()
    finally:
        session.close()


def _refresh_analytics(*, context: BenchmarkExperimentContext) -> None:
    """刷新压测实验的分钟聚合和 1 秒采样轨迹。"""

    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == context.experiment_id))
        if experiment is None:
            raise RuntimeError(f"压测实验不存在: {context.experiment_id}")

        decision_time_values = list(
            session.scalars(
                select(AgentDecision.simulation_time_ms)
                .where(AgentDecision.experiment_id == experiment.id)
                .distinct()
                .order_by(AgentDecision.simulation_time_ms)
            )
        )
        trajectory_time_values = list(
            session.scalars(
                select(TrajectoryPoint.simulation_time_ms)
                .where(TrajectoryPoint.experiment_id == experiment.id)
                .distinct()
                .order_by(TrajectoryPoint.simulation_time_ms)
            )
        )
        if trajectory_time_values:
            refresh_trajectory_metrics_for_times(
                session,
                experiment_pk=experiment.id,
                simulation_time_ms_values=trajectory_time_values,
            )
        if decision_time_values:
            refresh_decision_metrics_for_times(
                session,
                experiment_pk=experiment.id,
                simulation_time_ms_values=decision_time_values,
            )
        agent_metric_time_values = sorted({*decision_time_values, *trajectory_time_values})
        if agent_metric_time_values:
            refresh_agent_metrics_for_times(
                session,
                experiment_pk=experiment.id,
                simulation_time_ms_values=agent_metric_time_values,
            )
        if trajectory_time_values:
            agent_ids = list(
                session.scalars(
                    select(TrajectoryPoint.agent_id)
                    .where(TrajectoryPoint.experiment_id == experiment.id)
                    .distinct()
                    .order_by(TrajectoryPoint.agent_id)
                )
            )
            refresh_trajectory_samples_for_agents(
                session,
                experiment_pk=experiment.id,
                agent_ids=agent_ids,
                simulation_time_ms_values=trajectory_time_values,
            )
        session.commit()
    finally:
        session.close()


def _benchmark_analytics_endpoints(*, client: BenchmarkApiClient, context: BenchmarkExperimentContext) -> list[BenchmarkMetric]:
    """测量统计、轨迹、路径追踪和热力图相关分析接口。

    热力图会分别测冷缓存和热缓存：冷缓存前清理 Redis/数据库缓存，观察回源计算成本；
    热缓存用于观察正常重复查询的响应基线。
    """

    metrics: list[BenchmarkMetric] = []
    benchmark_agent_id = str(context.spawned_agents[0]["agent_id"])
    tourist_type_id = context.type_ids["tourist"]
    all_type_ids = [context.type_ids[type_code] for type_code, *_ in TYPE_BLUEPRINTS]
    heatmap_payload = {
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
    heatmap_compare_payload = {
        "time_start_ms": heatmap_payload["time_start_ms"],
        "time_end_ms": heatmap_payload["time_end_ms"],
        "grid_config": heatmap_payload["grid_config"],
        "metric": heatmap_payload["metric"],
        "crowd_types": all_type_ids,
        "normalization": heatmap_payload["normalization"],
    }

    metrics.append(
        _benchmark_generic_http_metric(
            key="stats",
            display_name="统计概览查询",
            threshold=PDF_THRESHOLDS["stats"],
            warmup_runs=1,
            measured_runs=10,
            operation=lambda: client.get(f"/api/v1/experiments/{context.experiment_id}/stats"),
        )
    )
    metrics.append(
        _benchmark_generic_http_metric(
            key="trajectories_compact",
            display_name="轨迹查询（compact 补充指标）",
            threshold=None,
            warmup_runs=1,
            measured_runs=10,
            operation=lambda: client.get(
                f"/api/v1/experiments/{context.experiment_id}/trajectories",
                params={"format": "compact", "page": 1, "page_size": 100},
            ),
        )
    )
    metrics.append(
        _benchmark_generic_http_metric(
            key="single_agent_trajectory",
            display_name="单人物轨迹查询",
            threshold=PDF_THRESHOLDS["single_agent_trajectory"],
            warmup_runs=1,
            measured_runs=10,
            operation=lambda: client.get(
                f"/api/v1/experiments/{context.experiment_id}/trajectories/{benchmark_agent_id}",
                params={"include_decisions": "true"},
            ),
        )
    )
    metrics.append(
        _benchmark_generic_http_metric(
            key="path_traces_100",
            display_name="路径追踪查询（100 人）",
            threshold=PDF_THRESHOLDS["path_traces_100"],
            warmup_runs=1,
            measured_runs=10,
            operation=lambda: client.get(
                f"/api/v1/experiments/{context.experiment_id}/path-traces",
                params={
                    "crowd_type": tourist_type_id,
                    "sample_rate": 1,
                    "max_agents": 100,
                    "time_start_ms": 10_000,
                    "time_end_ms": 69_000,
                },
            ),
        )
    )

    cold_heatmap_samples: list[BenchmarkSample] = []
    for _ in range(3):
        _clear_heatmap_cache(external_experiment_id=context.experiment_id)
        cold_heatmap_samples.append(
            _measure_http_call(
                operation=lambda payload_value=dict(heatmap_payload): client.post(
                    f"/api/v1/experiments/{context.experiment_id}/heatmap",
                    json=payload_value,
                ),
                expect_status=200,
                extra_builder=lambda response, elapsed_ms: {
                    "non_empty_cells": int(response.json()["data"]["statistics"]["non_empty_cells"]),
                    "total_cells": int(response.json()["data"]["statistics"]["total_cells"]),
                },
            )
        )
    metrics.append(
        BenchmarkMetric(
            key="heatmap_6000",
            display_name="热力图生成（6000 格，冷缓存）",
            unit="ms",
            warmup_runs=0,
            measured_runs=len(cold_heatmap_samples),
            samples=cold_heatmap_samples,
            threshold=PDF_THRESHOLDS["heatmap_6000"],
            notes=["每次测量前均清空数据库与 Redis 的热力图缓存。"],
        )
    )

    metrics.append(
        _benchmark_generic_http_metric(
            key="heatmap_warm",
            display_name="热力图生成（6000 格，热缓存补充指标）",
            threshold=None,
            warmup_runs=1,
            measured_runs=5,
            operation=lambda: client.post(
                f"/api/v1/experiments/{context.experiment_id}/heatmap",
                json=heatmap_payload,
            ),
        )
    )

    cold_compare_samples: list[BenchmarkSample] = []
    for _ in range(3):
        _clear_heatmap_cache(external_experiment_id=context.experiment_id)
        cold_compare_samples.append(
            _measure_http_call(
                operation=lambda payload_value=dict(heatmap_compare_payload): client.post(
                    f"/api/v1/experiments/{context.experiment_id}/heatmap/compare",
                    json=payload_value,
                ),
                expect_status=200,
                extra_builder=lambda response, elapsed_ms: {
                    "layer_count": len(response.json()["data"]["layers"]),
                },
            )
        )
    metrics.append(
        BenchmarkMetric(
            key="heatmap_compare_3",
            display_name="热力图对比（3 类，冷缓存）",
            unit="ms",
            warmup_runs=0,
            measured_runs=len(cold_compare_samples),
            samples=cold_compare_samples,
            threshold=PDF_THRESHOLDS["heatmap_compare_3"],
            notes=["每次测量前均清空数据库与 Redis 的热力图缓存。"],
        )
    )

    metrics.append(
        _benchmark_generic_http_metric(
            key="heatmap_compare_warm",
            display_name="热力图对比（3 类，热缓存补充指标）",
            threshold=None,
            warmup_runs=1,
            measured_runs=5,
            operation=lambda: client.post(
                f"/api/v1/experiments/{context.experiment_id}/heatmap/compare",
                json=heatmap_compare_payload,
            ),
        )
    )

    return metrics


def _benchmark_crowd_types_list(*, client: BenchmarkApiClient) -> BenchmarkMetric:
    """测量人群模板列表接口，作为低成本基础查询的参照项。"""

    return _benchmark_generic_http_metric(
        key="crowd_types_list",
        display_name="人群模板列表查询",
        threshold=PDF_THRESHOLDS["crowd_types_list"],
        warmup_runs=1,
        measured_runs=10,
        operation=lambda: client.get("/api/v1/crowd-types", params={"include_builtin": "true"}),
    )


def _benchmark_generic_http_metric(
    *,
    key: str,
    display_name: str,
    threshold: dict[str, Any] | None,
    warmup_runs: int,
    measured_runs: int,
    operation,
) -> BenchmarkMetric:
    """通用 HTTP 指标采样器，负责 warmup、正式采样和指标对象构造。"""

    for _ in range(warmup_runs):
        _measure_http_call(operation=operation, expect_status=200)

    samples = [
        _measure_http_call(operation=operation, expect_status=200)
        for _ in range(measured_runs)
    ]
    return BenchmarkMetric(
        key=key,
        display_name=display_name,
        unit="ms",
        warmup_runs=warmup_runs,
        measured_runs=measured_runs,
        samples=samples,
        threshold=threshold,
    )


def _seed_decision_history(
    *,
    session: Session,
    experiment_pk: int,
    agent_rows: list[ExperimentAgent],
    decision_targets: dict[str, dict[str, Any]],
) -> None:
    batch_rows: list[dict[str, Any]] = []
    for batch_index in range(12):
        simulation_time_ms = 120_000 + batch_index * 5_000
        batch_rows.append(
            {
                "experiment_id": experiment_pk,
                "batch_id": f"seed_decision_{batch_index}",
                "simulation_time_ms": simulation_time_ms,
                "requested_count": 100,
                "success_count": 90,
                "fallback_count": 10,
                "processing_duration_ms": 420 + batch_index * 8,
                "status": "completed",
                "response_payload": {"batch_id": f"seed_decision_{batch_index}", "total_count": 100},
                "processed_at": datetime.now(UTC),
            }
        )
    session.execute(insert(DecisionBatch), batch_rows)
    session.flush()

    batch_ids = {
        batch.batch_id: batch.id
        for batch in session.scalars(
            select(DecisionBatch).where(
                DecisionBatch.experiment_id == experiment_pk,
                DecisionBatch.batch_id.in_([f"seed_decision_{index}" for index in range(12)]),
            )
        ).all()
    }

    target_agents = agent_rows[:2000]
    decision_rows: list[dict[str, Any]] = []
    for batch_index in range(12):
        simulation_time_ms = 120_000 + batch_index * 5_000
        batch_pk = batch_ids[f"seed_decision_{batch_index}"]
        for agent_index, agent in enumerate(target_agents):
            target = decision_targets[agent.agent_id]
            source = DecisionSource.FALLBACK if agent_index % 10 == 0 else DecisionSource.LLM
            decision_rows.append(
                {
                    "experiment_id": experiment_pk,
                    "decision_batch_id": batch_pk,
                    "agent_id": agent.agent_id,
                    "simulation_time_ms": simulation_time_ms,
                    "crowd_type": agent.crowd_type,
                    "current_zone_id": "zone_lobby",
                    "stay_duration_ms": 10_000 + batch_index * 500,
                    "patience_ratio": min(1.2, 0.25 + batch_index * 0.05 + (agent_index % 7) * 0.03),
                    "algorithm_recommendation": target["exit_id"],
                    "available_exits_json": _build_available_exit_payload(agent.crowd_type, decision_targets),
                    "dilemma_summary": f"{agent.agent_id} 在主大厅等待出口决策。",
                    "chosen_exit_id": target["exit_id"],
                    "decision_source": source,
                    "reasoning": "当前目标区域吸引力更强，且等待成本可接受。" if source == DecisionSource.LLM else None,
                    "confidence": 0.86 if source == DecisionSource.LLM else 0.61,
                    "was_overridden": bool(agent_index % 6 == 0),
                    "llm_latency_ms": None if source == DecisionSource.FALLBACK else 120 + (agent_index % 15),
                }
            )

    for chunk in _chunked(decision_rows, 1000):
        session.execute(insert(AgentDecision), chunk)


def _seed_trajectory_history(
    *,
    session: Session,
    experiment_pk: int,
    agent_rows: list[ExperimentAgent],
    decision_targets: dict[str, dict[str, Any]],
) -> None:
    batch_rows: list[dict[str, Any]] = []
    point_rows: list[dict[str, Any]] = []
    total_batches = 60
    for batch_index in range(total_batches):
        simulation_time_ms = 10_000 + batch_index * 1_000
        batch_id = f"seed_traj_{batch_index}"
        batch_rows.append(
            {
                "experiment_id": experiment_pk,
                "batch_id": batch_id,
                "simulation_time_ms": simulation_time_ms,
                "sample_interval_ms": 1000,
                "point_count": len(agent_rows),
                "accepted_count": len(agent_rows),
                "rejected_count": 0,
            }
        )
        progress_ratio = batch_index / max(total_batches - 1, 1)
        for agent_index, agent in enumerate(agent_rows):
            target = decision_targets[agent.agent_id]
            point_rows.append(
                _build_trajectory_row(
                    experiment_pk=experiment_pk,
                    agent=agent,
                    target=target,
                    simulation_time_ms=simulation_time_ms,
                    sample_interval_ms=1000,
                    batch_id=batch_id,
                    agent_index=agent_index,
                    progress_ratio=progress_ratio,
                )
            )

    session.execute(insert(TrajectoryBatch), batch_rows)
    for chunk in _chunked(point_rows, 4000):
        session.execute(insert(TrajectoryPoint), chunk)


def _build_trajectory_row(
    *,
    experiment_pk: int,
    agent: ExperimentAgent,
    target: dict[str, Any],
    simulation_time_ms: int,
    sample_interval_ms: int,
    batch_id: str,
    agent_index: int,
    progress_ratio: float,
) -> dict[str, Any]:
    spawn_x = float(agent.spawn_x)
    spawn_z = float(agent.spawn_z)
    target_x = float(target["target_x"])
    target_z = float(target["target_z"])
    lane_offset_x = ((agent_index % 9) - 4) * 0.18
    lane_offset_z = ((agent_index % 11) - 5) * 0.16
    position_x = spawn_x + (target_x - spawn_x) * progress_ratio + lane_offset_x
    position_z = spawn_z + (target_z - spawn_z) * progress_ratio + lane_offset_z

    if progress_ratio < 0.20:
        state = AgentState.WALKING
        zone_id = "zone_lobby"
    elif progress_ratio < 0.35:
        state = AgentState.TRANSITIONING
        zone_id = "zone_lobby"
    elif progress_ratio < 0.85:
        state = AgentState.WALKING
        zone_id = str(target["zone_id"])
    elif agent_index % 5 == 0:
        state = AgentState.QUEUING
        zone_id = str(target["zone_id"])
    else:
        state = AgentState.IDLE
        zone_id = str(target["zone_id"])

    return {
        "experiment_id": experiment_pk,
        "agent_id": agent.agent_id,
        "simulation_time_ms": simulation_time_ms,
        "crowd_type": agent.crowd_type,
        "zone_id": zone_id,
        "position_x": round_coordinate(position_x),
        "position_z": round_coordinate(position_z),
        "speed": round(max(0.25, 1.35 - progress_ratio * 0.75 + ((agent_index % 5) - 2) * 0.04), 2),
        "state": state,
        "target_exit_id": str(target["exit_id"]),
        "is_queuing": state == AgentState.QUEUING,
        "sample_interval_ms": sample_interval_ms,
        "batch_id": batch_id,
        "ingested_at": datetime.now(UTC),
    }


def _build_decision_request_payload(
    *,
    context: BenchmarkExperimentContext,
    benchmark_agents: list[dict[str, Any]],
    decision_targets: dict[str, dict[str, Any]],
    batch_id: str,
    simulation_time_ms: int,
) -> dict[str, Any]:
    type_code_by_type_id = {
        context.type_ids[type_code]: type_code
        for type_code, *_ in TYPE_BLUEPRINTS
    }
    agents: list[dict[str, Any]] = []
    for agent_index, agent in enumerate(benchmark_agents):
        crowd_type = str(agent["crowd_type"])
        type_code = type_code_by_type_id.get(crowd_type, "tourist")
        prompt_context = _build_decision_prompt_context(
            type_code=type_code,
            agent_index=agent_index,
            simulation_time_ms=simulation_time_ms,
        )
        agents.append(
            {
                "agent_id": agent["agent_id"],
                "crowd_type": crowd_type,
                "current_zone_id": "zone_lobby",
                "stay_duration_ms": 12_000 + agent_index * 20,
                "patience_ratio": prompt_context["patience_ratio"],
                "algorithm_recommendation": prompt_context["algorithm_recommendation"],
                "dilemma_summary": prompt_context["dilemma_summary"],
                "available_exits": _build_available_exit_payload(
                    crowd_type,
                    decision_targets,
                    preferred_exit_id=prompt_context["preferred_exit_id"],
                    agent_index=agent_index,
                    simulation_time_ms=simulation_time_ms,
                    intent_label=prompt_context["intent_label"],
                ),
            }
        )

    return {
        "batch_id": batch_id,
        "simulation_time_ms": simulation_time_ms,
        "agents": agents,
    }


def _build_available_exit_payload(
    crowd_type: str,
    decision_targets: dict[str, dict[str, Any]] | None = None,
    *,
    preferred_exit_id: str | None = None,
    agent_index: int = 0,
    simulation_time_ms: int = 0,
    intent_label: str | None = None,
) -> list[dict[str, Any]]:
    resolved_preferred_exit_id = preferred_exit_id
    if resolved_preferred_exit_id is None and decision_targets is not None:
        for item in decision_targets.values():
            if item["crowd_type"] == crowd_type:
                resolved_preferred_exit_id = str(item["exit_id"])
                break

    result: list[dict[str, Any]] = []
    phase = (simulation_time_ms // 5000 + agent_index) % 7
    for exit_index, (type_code, _, _, zone_id, exit_id) in enumerate(TYPE_BLUEPRINTS):
        wait_seconds = 6 + ((agent_index * 3 + exit_index * 5 + phase) % 18)
        density_level = ("较低", "中等", "偏高", "较高")[(agent_index + exit_index + phase) % 4]
        walking_seconds = 10 + exit_index * 6 + ((agent_index + phase) % 5)
        score = _build_benchmark_algorithm_score(
            exit_id=exit_id,
            preferred_exit_id=resolved_preferred_exit_id,
            exit_index=exit_index,
            agent_index=agent_index,
            phase=phase,
        )
        result.append(
            {
                "exit_id": exit_id,
                "target_zone_id": zone_id,
                "algorithm_score": score,
                "nl_description": (
                    f"通往{ZONE_DISPLAY_NAMES.get(zone_id, zone_id)}，预计步行约 {walking_seconds} 秒，"
                    f"当前排队约 {wait_seconds} 秒，人流密度{density_level}；"
                    f"{intent_label or '系统已综合考虑该方向的通行效率与目标吸引力'}。"
                ),
            }
        )
    return result


def _build_decision_prompt_context(
    *,
    type_code: str,
    agent_index: int,
    simulation_time_ms: int,
) -> dict[str, Any]:
    scenarios = DECISION_SCENARIOS.get(type_code, DECISION_SCENARIOS["tourist"])
    scenario = scenarios[(agent_index + simulation_time_ms // 5000) % len(scenarios)]
    preferred_exit_id = next(
        exit_id
        for blueprint_type_code, _, _, zone_id, exit_id in TYPE_BLUEPRINTS
        if zone_id == scenario["preferred_zone_id"]
    )
    patience_ratio = min(
        1.2,
        max(0.2, 0.35 + (agent_index % 10) * 0.07 + float(scenario["urgency_bias"])),
    )
    algorithm_recommendation = preferred_exit_id if (agent_index + simulation_time_ms // 1000) % 6 != 0 else None
    return {
        "preferred_exit_id": preferred_exit_id,
        "algorithm_recommendation": algorithm_recommendation,
        "patience_ratio": round(patience_ratio, 2),
        "dilemma_summary": str(scenario["summary_template"]),
        "intent_label": str(scenario["intent_label"]),
    }


def _build_benchmark_algorithm_score(
    *,
    exit_id: str,
    preferred_exit_id: str | None,
    exit_index: int,
    agent_index: int,
    phase: int,
) -> float:
    if preferred_exit_id is not None and exit_id == preferred_exit_id:
        score = 0.78 + ((agent_index + phase) % 9) * 0.02
    else:
        score = 0.38 + ((agent_index * 2 + exit_index + phase) % 8) * 0.035
    return round(min(score, 0.96), 2)


def _build_trajectory_payload(
    *,
    benchmark_agents: list[dict[str, Any]],
    decision_targets: dict[str, dict[str, Any]],
    batch_id: str,
    simulation_time_ms: int,
    progress_ratio: float,
) -> dict[str, Any]:
    points: list[dict[str, Any]] = []
    for agent_index, agent in enumerate(benchmark_agents):
        target = decision_targets[str(agent["agent_id"])]
        spawn_x = float(agent["spawn_x"])
        spawn_z = float(agent["spawn_z"])
        target_x = float(target["target_x"])
        target_z = float(target["target_z"])
        position_x = spawn_x + (target_x - spawn_x) * progress_ratio + ((agent_index % 9) - 4) * 0.18
        position_z = spawn_z + (target_z - spawn_z) * progress_ratio + ((agent_index % 7) - 3) * 0.14
        if progress_ratio < 0.2:
            state = "walking"
            zone_id = "zone_lobby"
        elif progress_ratio < 0.35:
            state = "transitioning"
            zone_id = "zone_lobby"
        elif progress_ratio < 0.85:
            state = "walking"
            zone_id = target["zone_id"]
        elif agent_index % 6 == 0:
            state = "queuing"
            zone_id = target["zone_id"]
        else:
            state = "idle"
            zone_id = target["zone_id"]
        points.append(
            {
                "agent_id": agent["agent_id"],
                "crowd_type": agent["crowd_type"],
                "position": {"x": round_coordinate(position_x), "z": round_coordinate(position_z)},
                "zone_id": zone_id,
                "speed": round(max(0.3, 1.25 - progress_ratio * 0.55 + ((agent_index % 5) - 2) * 0.04), 2),
                "state": state,
                "target_exit_id": target["exit_id"],
                "is_queuing": state == "queuing",
            }
        )
    return {
        "batch_id": batch_id,
        "simulation_time_ms": simulation_time_ms,
        "sample_interval_ms": 1000,
        "points": points,
    }


def _build_agent_target_map(context: BenchmarkExperimentContext) -> dict[str, dict[str, Any]]:
    target_by_type: dict[str, dict[str, Any]] = {
        context.type_ids[type_code]: {
            "crowd_type": context.type_ids[type_code],
            "zone_id": zone_id,
            "exit_id": exit_id,
            "target_x": 110.0 if zone_id == "zone_exhibit" else 30.0 if zone_id == "zone_rest" else 110.0,
            "target_z": 25.0 if zone_id == "zone_exhibit" else 110.0 if zone_id == "zone_rest" else 110.0,
        }
        for type_code, _, _, zone_id, exit_id in TYPE_BLUEPRINTS
    }
    return {
        str(agent["agent_id"]): {
            **target_by_type[str(agent["crowd_type"])],
            "crowd_type": str(agent["crowd_type"]),
        }
        for agent in context.spawned_agents
    }


def _clear_heatmap_cache(*, external_experiment_id: str) -> None:
    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == external_experiment_id))
        if experiment is None:
            return
        session.execute(delete(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id))
        session.commit()
    finally:
        session.close()

    redis_client = get_redis_client()
    redis_client.delete_by_pattern("heatmap", "*")


def _build_trajectory_ingest_extra(*, response, elapsed_ms: float, batch_id: str) -> dict[str, Any]:
    payload = response.json()["data"]
    accepted_count = int(payload["accepted_count"])
    elapsed_seconds = max(elapsed_ms / 1000.0, 0.001)
    return {
        "batch_id": batch_id,
        "accepted_count": accepted_count,
        "rejected_count": int(payload["rejected_count"]),
        "throughput_rps": round(accepted_count / elapsed_seconds, 2),
        "total_stored_points": int(payload["total_stored_points"]),
    }


def _normalize_spawned_agents(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "agent_id": str(item["agent_id"]),
            "crowd_type": str(item["type_id"]),
            "spawn_zone_id": str(item["zone_id"]),
            "spawn_x": float(item["position"]["x"]),
            "spawn_z": float(item["position"]["z"]),
        }
        for item in items
    ]


def _build_dataset_summary(*, context: BenchmarkExperimentContext) -> dict[str, Any]:
    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == context.experiment_id))
        if experiment is None:
            raise RuntimeError(f"压测实验不存在: {context.experiment_id}")
        trajectory_point_count = session.scalar(
            select(func.count()).select_from(TrajectoryPoint).where(TrajectoryPoint.experiment_id == experiment.id)
        ) or 0
        sampled_point_count = session.scalar(
            select(func.count()).select_from(TrajectoryPointSampled1s).where(
                TrajectoryPointSampled1s.experiment_id == experiment.id
            )
        ) or 0
        decision_count = session.scalar(
            select(func.count()).select_from(AgentDecision).where(AgentDecision.experiment_id == experiment.id)
        ) or 0
        decision_batch_count = session.scalar(
            select(func.count()).select_from(DecisionBatch).where(DecisionBatch.experiment_id == experiment.id)
        ) or 0
        agent_metric_bucket_count = session.scalar(
            select(func.count()).select_from(AgentMetric1m).where(AgentMetric1m.experiment_id == experiment.id)
        ) or 0
    finally:
        session.close()

    return {
        "experiment_id": context.experiment_id,
        "total_agents": len(context.spawned_agents),
        "crowd_types": context.type_ids,
        "allocation_counts": context.allocation_counts,
        "trajectory_points": int(trajectory_point_count),
        "trajectory_points_sampled_1s": int(sampled_point_count),
        "agent_decisions": int(decision_count),
        "decision_batches": int(decision_batch_count),
        "agent_metric_buckets": int(agent_metric_bucket_count),
        "heatmap_grid": {"cols": 100, "rows": 60, "total_cells": 6000},
    }


def _build_report(results: dict[str, Any]) -> str:
    dataset = results["dataset"]
    metrics: list[dict[str, Any]] = list(results["metrics"])
    passed_count = sum(1 for metric in metrics if metric["status"] == "通过")
    threshold_metric_count = sum(1 for metric in metrics if metric["threshold"] is not None)
    decision_engine_mode = str(results["environment"]["decision_engine_mode"])
    if decision_engine_mode in {"heuristic", "mock", "local"}:
        decision_engine_note = "- 决策批次当前使用的是后端 `heuristic` 引擎模式，因此该项验证的是“后端批量调度 + 有限并发 + Fallback 逻辑”的性能基线，不代表外部真实大模型供应商的最终 SLA。"
    else:
        decision_engine_note = f"- 决策批次当前使用的是 `{decision_engine_mode}` 引擎模式，结果会同时受到外部模型供应商延迟、限流与可用性的影响。"

    lines = [
        "# 正式压测报告",
        "",
        f"- 生成时间：{results['generated_at']}",
        f"- 压测实验：`{dataset['experiment_id']}`",
        f"- 方法口径：{results['environment']['methodology']}",
        f"- 决策引擎模式：`{results['environment']['decision_engine_mode']}`",
        f"- 决策并发上限：`{results['environment']['decision_batch_max_concurrency']}`",
        f"- 结论概览：`{passed_count}/{threshold_metric_count}` 项已通过当前 PDF 阈值校验",
        "",
        "## 环境信息",
        "",
        f"- Python：`{results['environment']['python_version']}`",
        f"- 平台：`{results['environment']['platform']}`",
        f"- PostgreSQL：`{results['environment']['server_version']}`",
        f"- TimescaleDB：`{results['environment']['timescaledb_version']}`",
        f"- Redis：`{'启用' if results['environment']['redis_enabled'] else '未启用'}`",
        "",
        "## 数据集规模",
        "",
        f"- 总人数：`{dataset['total_agents']}`",
        f"- 人群类型：`{len(dataset['crowd_types'])}` 类",
        f"- 原始轨迹点：`{dataset['trajectory_points']}`",
        f"- 秒级采样轨迹点：`{dataset['trajectory_points_sampled_1s']}`",
        f"- 决策记录：`{dataset['agent_decisions']}`",
        f"- 决策批次：`{dataset['decision_batches']}`",
        f"- 人物分钟聚合桶：`{dataset['agent_metric_buckets']}`",
        f"- 热力图网格：`{dataset['heatmap_grid']['cols']} x {dataset['heatmap_grid']['rows']} = {dataset['heatmap_grid']['total_cells']}`",
        "",
        "## 结果总表",
        "",
        "| 指标 | 平均耗时(ms) | P95(ms) | P99(ms) | 最大耗时(ms) | 平均吞吐 | 阈值对照 | 结论 |",
        "| --- | ---: | ---: | ---: | ---: | ---: | --- | --- |",
    ]

    for metric in metrics:
        avg_throughput = metric["avg_throughput_rps"]
        throughput_text = "-" if avg_throughput is None else f"{avg_throughput:.2f}/s"
        lines.append(
            "| {name} | {avg:.2f} | {p95:.2f} | {p99:.2f} | {maxv:.2f} | {throughput} | {threshold} | {status} |".format(
                name=metric["display_name"],
                avg=metric["avg_ms"],
                p95=metric["p95_ms"],
                p99=metric["p99_ms"],
                maxv=metric["max_ms"],
                throughput=throughput_text,
                threshold=metric["threshold_summary"],
                status=metric["status"],
            )
        )

    lines.extend(
        [
            "",
            "## 关键说明",
            "",
            "- 本次压测覆盖了 PDF 9.4 中当前可以在本机完整验证的主要性能项，包括模板列表、生成位置、决策批次、轨迹写入、统计概览、轨迹查询、路径追踪、热力图和热力图对比。",
            decision_engine_note,
            "- 热力图与热力图对比的正式阈值对照使用的是“冷缓存”结果；“热缓存”结果单列为补充指标，方便评估 Redis 与数据库缓存命中的收益。",
            "- `/trajectories` 的 `compact` 查询属于当前重构后的补充观测指标，便于持续追踪采样层效果，PDF 9.4 并未单独给它设置阈值。",
            "",
            "## 指标细节",
            "",
        ]
    )

    for metric in metrics:
        lines.append(f"### {metric['display_name']}")
        lines.append("")
        lines.append(f"- 测量次数：预热 `{metric['warmup_runs']}` 次，正式 `{metric['measured_runs']}` 次")
        lines.append(f"- 单次耗时：`{', '.join(f'{value:.2f}' for value in metric['durations_ms'])}`")
        if metric["avg_throughput_rps"] is not None:
            lines.append(
                f"- 吞吐：平均 `{metric['avg_throughput_rps']:.2f}` 点/秒，最低 `{metric['min_throughput_rps']:.2f}` 点/秒"
            )
        if metric["notes"]:
            for note in metric["notes"]:
                lines.append(f"- 备注：{note}")
        lines.append("")

    return "\n".join(lines).strip() + "\n"


def _measure_http_call(
    *,
    operation,
    expect_status: int,
    extra_builder=None,
) -> BenchmarkSample:
    started_at = perf_counter()
    response = operation()
    elapsed_ms = (perf_counter() - started_at) * 1000
    if response.status_code != expect_status:
        raise RuntimeError(
            f"压测请求失败: status={response.status_code} expect={expect_status} body={response.text}"
        )
    extra = extra_builder(response, elapsed_ms) if extra_builder is not None else {}
    if response.headers.get("content-type", "").startswith("application/json"):
        try:
            response_json = response.json()
            if isinstance(response_json, dict) and "data" in response_json and isinstance(response_json["data"], dict):
                if "agents" in response_json["data"]:
                    extra["_agents"] = list(response_json["data"]["agents"])
        except Exception:
            pass
    return BenchmarkSample(duration_ms=round(elapsed_ms, 2), status_code=response.status_code, extra=extra)


def _assert_success(response, *, expected_status: int, label: str) -> None:
    if response.status_code != expected_status:
        raise RuntimeError(f"{label}失败: status={response.status_code} body={response.text}")


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return round(ordered[0], 2)
    index = (len(ordered) - 1) * ratio
    lower = floor(index)
    upper = ceil(index)
    if lower == upper:
        return round(ordered[lower], 2)
    lower_value = ordered[lower]
    upper_value = ordered[upper]
    weight = index - lower
    return round(lower_value + (upper_value - lower_value) * weight, 2)


def _chunked(items: list[dict[str, Any]], chunk_size: int) -> list[list[dict[str, Any]]]:
    return [items[index : index + chunk_size] for index in range(0, len(items), chunk_size)]
