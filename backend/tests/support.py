"""跨测试目录复用的场景与环境辅助函数。"""

from __future__ import annotations

from dataclasses import replace
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.api import idempotency as idempotency_api
from backend.app.application.maintenance import service as maintenance_service
from backend.app.core.config import Settings, get_settings as load_settings
from backend.app.infrastructure.cache import close_redis_client, get_redis_client
from backend.app.infrastructure.cache import redis_client as redis_client_module
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    CrowdTypeTemplate,
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
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.workers import rq_worker


def build_scoped_backend_settings(monkeypatch) -> Settings:
    """为真实 Redis / RQ 集成测试创建隔离配置。"""

    base_settings = load_settings()
    if not base_settings.redis_enabled:
        pytest.skip("当前环境未启用 Redis，跳过 integration / e2e 实测")

    redis_health = get_redis_client().healthcheck()
    if redis_health.get("status") != "ok":
        pytest.skip(f"Redis 当前不可用，跳过 integration / e2e 实测: {redis_health.get('message')}")

    suffix = uuid4().hex[:8]
    scoped_settings = replace(
        base_settings,
        redis_key_prefix=f"{base_settings.redis_key_prefix}_{suffix}",
        maintenance_queue_name=f"{base_settings.maintenance_queue_name}_{suffix}",
        maintenance_rq_ttl_seconds=60,
        maintenance_rq_result_ttl_seconds=1,
        maintenance_rq_failure_ttl_seconds=1,
        request_idempotency_ttl_seconds=60,
        spawn_positions_cache_ttl_seconds=60,
    )

    monkeypatch.setattr(redis_client_module, "get_settings", lambda: scoped_settings)
    monkeypatch.setattr(rq_worker, "get_settings", lambda: scoped_settings)
    monkeypatch.setattr(idempotency_api, "get_settings", lambda: scoped_settings)
    monkeypatch.setattr(maintenance_service, "get_settings", lambda: scoped_settings)
    close_redis_client()
    return scoped_settings


def cleanup_scoped_redis_artifacts(*, queue_name: str) -> None:
    """清理测试专用 Redis 缓存前缀与 RQ 队列。"""

    close_redis_client()
    client = get_redis_client()
    connection = client.get_rq_connection()
    if connection is not None:
        queue_class, _ = rq_worker._import_rq_components()
        queue = queue_class(queue_name, connection=connection)
        queue.empty()

    client.delete_by_pattern("*")
    close_redis_client()


def prepare_running_experiment(
    client: TestClient,
    *,
    suffix: str,
    type_id: str,
    batch_size: int,
    fallback_to_algorithm: bool,
    agent_count: int,
    hall_zone_type: str = "plaza",
    entry_capacity: int = 100,
    create_template: bool = True,
) -> tuple[str, list[dict[str, Any]]]:
    """创建并启动一个可直接用于运行期 API 的实验。"""

    if create_template:
        create_template_response = client.post(
            "/api/v1/crowd-types",
            json={
                "type_id": type_id,
                "display_name": "集成测试模板",
                "color_hex": "#FF6B35",
                "base_speed": 1.15,
                "social_distance": 0.8,
                "tolerance_duration_ms": 24000,
                "attraction_weights": {
                    "exhibit": 0.5,
                    "commercial": 0.2,
                    "rest": 0.2,
                    "service": 0.2,
                    "plaza": 0.4,
                    "passage": 0.6,
                    "exit_final": 0.1,
                },
                "metadata": {},
            },
        )
        assert create_template_response.status_code == 201

    create_experiment_response = client.post(
        "/api/v1/experiments",
        json={
            "name": f"集成实验-{suffix}",
            "description": "用于 integration / e2e 回归",
            "map_id": "demo-map",
            "llm_config": {
                "model": "gpt-4.1-mini",
                "temperature": 0.6,
                "max_tokens": 512,
                "timeout_ms": 5000,
            },
            "decision_config": {
                "batch_interval_ms": 1000,
                "batch_size": batch_size,
                "fallback_to_algorithm": fallback_to_algorithm,
            },
            "tags": ["integration-e2e"],
        },
    )
    assert create_experiment_response.status_code == 201
    experiment_id = create_experiment_response.json()["data"]["experiment_id"]
    client.headers.update({"X-Experiment-Id": experiment_id})

    topology_response = client.put(
        f"/api/v1/experiments/{experiment_id}/topology",
        json={
            "zones": [
                {
                    "zone_id": "entry_a",
                    "zone_name": "入口A",
                    "zone_type": "passage",
                    "bounds": {"min_x": 0, "min_z": 0, "max_x": 12, "max_z": 12},
                    "capacity": entry_capacity,
                    "is_entry_zone": True,
                    "metadata": {},
                },
                {
                    "zone_id": "hall_1",
                    "zone_name": "大厅1",
                    "zone_type": hall_zone_type,
                    "bounds": {"min_x": 15, "min_z": 0, "max_x": 35, "max_z": 20},
                    "capacity": 200,
                    "is_entry_zone": False,
                    "metadata": {},
                },
            ],
            "exits": [
                {
                    "exit_id": "exit_1",
                    "source_zone_id": "entry_a",
                    "target_zone_id": "hall_1",
                    "exit_type": "portal",
                    "position": {"x": 13, "z": 6},
                    "pass_capacity_per_sec": 20,
                    "metadata": {},
                }
            ],
        },
    )
    assert topology_response.status_code == 200

    allocation_response = client.put(
        f"/api/v1/experiments/{experiment_id}/crowd-allocation",
        json={
            "groups": [
                {
                    "type_id": type_id,
                    "count": agent_count,
                    "distribution": {
                        "mode": "zone_fixed",
                        "fixed_zone_id": "entry_a",
                        "spread_radius": 2.5,
                        "avoid_exits": True,
                    },
                }
            ],
            "agent_id_prefix": f"itg_{suffix[:4]}",
        },
    )
    assert allocation_response.status_code == 200

    spawn_response = client.post(
        f"/api/v1/experiments/{experiment_id}/spawn-positions",
        json={"seed": 77},
    )
    assert spawn_response.status_code == 200
    spawned_agents = spawn_response.json()["data"]["agents"]
    assert len(spawned_agents) == agent_count

    start_response = client.post(
        f"/api/v1/experiments/{experiment_id}/start",
        json={"actual_start_time_ms": 1700000200000},
    )
    assert start_response.status_code == 200
    return experiment_id, spawned_agents


def cleanup_experiment_graph(*, experiment_id: str | None, type_id: str | None = None) -> None:
    """删除测试创建的实验图及其可选模板。"""

    session = get_session_factory()()
    try:
        if experiment_id is not None:
            session.execute(delete(ExperimentArchive).where(ExperimentArchive.experiment_id == experiment_id))
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
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
                session.execute(
                    delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
                )
                session.execute(delete(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment.id))
                session.execute(delete(ExperimentAllocation).where(ExperimentAllocation.experiment_id == experiment.id))
                session.execute(delete(ExperimentExit).where(ExperimentExit.experiment_id == experiment.id))
                session.execute(delete(ExperimentZone).where(ExperimentZone.experiment_id == experiment.id))
                session.execute(delete(Experiment).where(Experiment.id == experiment.id))

        if type_id is not None:
            session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id == type_id))

        session.commit()
    finally:
        session.close()
