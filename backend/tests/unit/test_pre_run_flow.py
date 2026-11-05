"""实验启动前主链路回归测试。"""

from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.application import allocation_spawn as allocation_spawn_package
from backend.app.application.allocation_spawn import service as allocation_spawn_service
from backend.app.api import idempotency as idempotency_api
from backend.app.infrastructure.db.models import (
    CrowdTypeTemplate,
    DecisionMetric1m,
    ExitMetric1m,
    Experiment,
    ExperimentAgent,
    ExperimentAllocation,
    ExperimentExit,
    ExperimentMaintenanceJob,
    ExperimentZone,
    HeatmapCache,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.main import create_app
from backend.tests.conftest import create_api_client


class FakeRedisClient:
    """用于测试的内存版 Redis 客户端。"""

    def __init__(self) -> None:
        self.store: dict[tuple[str, ...], object] = {}

    def get_json(self, *parts: object) -> dict[str, object] | None:
        value = self.store.get(tuple(str(part) for part in parts))
        return deepcopy(value) if isinstance(value, dict) else None

    def set_json(self, *parts: object, value: dict[str, object], ttl_seconds: int | None = None) -> bool:
        self.store[tuple(str(part) for part in parts)] = deepcopy(value)
        return True

    def set_if_absent(self, *parts: object, value: str, ttl_seconds: int | None = None) -> bool:
        key = tuple(str(part) for part in parts)
        if key in self.store:
            return False
        self.store[key] = str(value)
        return True

    def delete_if_value(self, *parts: object, expected_value: str) -> bool:
        key = tuple(str(part) for part in parts)
        if self.store.get(key) != expected_value:
            return False
        self.store.pop(key, None)
        return True

    def delete_by_pattern(self, *parts: object) -> int:
        prefix = tuple(str(part) for part in parts[:-1]) if parts and str(parts[-1]) == "*" else tuple(str(part) for part in parts)
        keys = [key for key in self.store if key[: len(prefix)] == prefix]
        for key in keys:
            self.store.pop(key, None)
        return len(keys)


def test_pre_run_flow_can_spawn_positions_start_and_complete() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"tourist_{suffix}"
    experiment_id: str | None = None

    try:
        create_template_response = client.post(
            "/api/v1/crowd-types",
            json={
                "type_id": type_id,
                "display_name": "联调用游客",
                "color_hex": "#FF6B35",
                "base_speed": 1.2,
                "social_distance": 0.8,
                "tolerance_duration_ms": 30000,
                "attraction_weights": {
                    "exhibit": 0.6,
                    "commercial": 0.3,
                    "rest": 0.2,
                    "service": 0.2,
                    "plaza": 0.4,
                    "passage": 0.5,
                    "exit_final": 0.1,
                },
                "metadata": {},
            },
        )
        assert create_template_response.status_code == 201

        create_experiment_response = client.post(
            "/api/v1/experiments",
            json={
                "name": f"主链路测试-{suffix}",
                "description": "用于回归 spawn-positions 和 start",
                "map_id": "demo-map",
                "llm_config": {
                    "model": "gpt-4.1-mini",
                    "temperature": 0.6,
                    "max_tokens": 512,
                    "timeout_ms": 5000,
                },
                "decision_config": {
                    "batch_interval_ms": 1000,
                    "batch_size": 20,
                    "fallback_to_algorithm": True,
                },
                "tags": ["test"],
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
                        "capacity": 100,
                        "is_entry_zone": True,
                        "metadata": {},
                    },
                    {
                        "zone_id": "hall_1",
                        "zone_name": "大厅1",
                        "zone_type": "plaza",
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
                        "count": 6,
                        "distribution": {
                            "mode": "zone_weighted",
                            "spread_radius": 2.5,
                            "avoid_exits": True,
                            "zone_weights": [
                                {"zone_id": "entry_a", "weight": 0.5},
                                {"zone_id": "hall_1", "weight": 0.5},
                            ],
                        },
                    }
                ],
                "agent_id_prefix": f"sim_{suffix[:4]}",
            },
        )
        assert allocation_response.status_code == 200
        assert allocation_response.json()["data"]["status"] == "configured"

        spawn_response = client.post(
            f"/api/v1/experiments/{experiment_id}/spawn-positions",
            json={"seed": 42},
        )
        assert spawn_response.status_code == 200
        spawn_payload = spawn_response.json()["data"]
        assert spawn_payload["seed"] == 42
        assert spawn_payload["total_count"] == 6
        assert len(spawn_payload["agents"]) == 6
        assert spawn_payload["agents"][0]["agent_id"].startswith(f"sim_{suffix[:4]}_")

        same_seed_response = client.post(
            f"/api/v1/experiments/{experiment_id}/spawn-positions",
            json={"seed": 42},
        )
        assert same_seed_response.status_code == 200
        assert same_seed_response.json()["data"] == spawn_payload

        start_response = client.post(
            f"/api/v1/experiments/{experiment_id}/start",
            json={"actual_start_time_ms": 1700000100000},
        )
        assert start_response.status_code == 200
        start_payload = start_response.json()["data"]
        assert start_payload["experiment_id"] == experiment_id
        assert start_payload["status"] == "running"
        assert start_payload["started_at_ms"] == 1700000100000

        start_again_response = client.post(
            f"/api/v1/experiments/{experiment_id}/start",
            json={"actual_start_time_ms": 1700000100001},
        )
        assert start_again_response.status_code == 409
        assert start_again_response.json()["code"] == 2002

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 60000,
                "final_agent_count": 6,
            },
        )
        assert complete_response.status_code == 200
        complete_payload = complete_response.json()["data"]
        assert complete_payload["experiment_id"] == experiment_id
        assert complete_payload["status"] == "completed"
        assert complete_payload["summary"]["duration_ms"] == 60000
        assert complete_payload["summary"]["total_trajectory_points"] == 0
        assert complete_payload["summary"]["total_decision_requests"] == 0
        assert complete_payload["summary"]["total_llm_decisions"] == 0
        assert complete_payload["summary"]["llm_fallback_count"] == 0
        assert complete_payload["summary"]["crowd_type_stats"] == [
            {
                "type_id": type_id,
                "final_count": 6,
                "exited_count": 0,
            }
        ]

        complete_again_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 60001,
                "final_agent_count": 6,
            },
        )
        assert complete_again_response.status_code == 409
        assert complete_again_response.json()["code"] == 2002
    finally:
        _cleanup_pre_run_flow(experiment_id=experiment_id, type_id=type_id)


def test_topology_requires_at_least_one_entry_zone() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    experiment_id: str | None = None

    try:
        create_experiment_response = client.post(
            "/api/v1/experiments",
            json={
                "name": f"入口区校验测试-{suffix}",
                "description": "验证 PDF 要求拓扑至少包含一个入口区域",
                "map_id": "demo-map",
                "llm_config": {
                    "model": "gpt-4.1-mini",
                    "temperature": 0.6,
                    "max_tokens": 512,
                    "timeout_ms": 5000,
                },
                "decision_config": {
                    "batch_interval_ms": 1000,
                    "batch_size": 20,
                    "fallback_to_algorithm": True,
                },
                "tags": ["topology-validation"],
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
                        "zone_id": "hall_1",
                        "zone_name": "大厅1",
                        "zone_type": "plaza",
                        "bounds": {"min_x": 0, "min_z": 0, "max_x": 20, "max_z": 20},
                        "capacity": 200,
                        "is_entry_zone": False,
                        "metadata": {},
                    }
                ],
                "exits": [],
            },
        )

        assert topology_response.status_code == 400
        payload = topology_response.json()
        assert payload["code"] == 2003
        assert "is_entry_zone=true" in payload["data"]["validation_errors"][0]
    finally:
        _cleanup_experiment_only(experiment_id=experiment_id)


def test_configured_experiment_rejects_topology_and_allocation_updates(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"spawn_cache_{suffix}"
    experiment_id: str | None = None
    fake_redis = FakeRedisClient()

    try:
        monkeypatch.setattr(allocation_spawn_service, "get_redis_client", lambda: fake_redis)

        create_template_response = client.post(
            "/api/v1/crowd-types",
            json={
                "type_id": type_id,
                "display_name": "缓存联调游客",
                "color_hex": "#FF6B35",
                "base_speed": 1.2,
                "social_distance": 0.8,
                "tolerance_duration_ms": 30000,
                "attraction_weights": {
                    "exhibit": 0.6,
                    "commercial": 0.3,
                    "rest": 0.2,
                    "service": 0.2,
                    "plaza": 0.4,
                    "passage": 0.5,
                    "exit_final": 0.1,
                },
                "metadata": {},
            },
        )
        assert create_template_response.status_code == 201

        create_experiment_response = client.post(
            "/api/v1/experiments",
            json={
                "name": f"出生点缓存测试-{suffix}",
                "description": "用于回归 spawn-positions Redis 缓存",
                "map_id": "demo-map",
                "llm_config": {
                    "model": "gpt-4.1-mini",
                    "temperature": 0.6,
                    "max_tokens": 512,
                    "timeout_ms": 5000,
                },
                "decision_config": {
                    "batch_interval_ms": 1000,
                    "batch_size": 20,
                    "fallback_to_algorithm": True,
                },
                "tags": ["redis-spawn-test"],
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
                        "capacity": 100,
                        "is_entry_zone": True,
                        "metadata": {},
                    }
                ],
                "exits": [],
            },
        )
        assert topology_response.status_code == 200

        allocation_response = client.put(
            f"/api/v1/experiments/{experiment_id}/crowd-allocation",
            json={
                "groups": [
                    {
                        "type_id": type_id,
                        "count": 3,
                        "distribution": {
                            "mode": "zone_fixed",
                            "fixed_zone_id": "entry_a",
                            "spread_radius": 2.5,
                            "avoid_exits": True,
                        },
                    }
                ],
                "agent_id_prefix": f"cache_{suffix[:4]}",
            },
        )
        assert allocation_response.status_code == 200

        first_spawn_response = client.post(
            f"/api/v1/experiments/{experiment_id}/spawn-positions",
            json={"seed": 2024},
        )
        assert first_spawn_response.status_code == 200
        first_payload = first_spawn_response.json()["data"]
        assert len(fake_redis.store) == 1

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            session.execute(delete(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment.id))
            session.commit()
        finally:
            session.close()

        second_spawn_response = client.post(
            f"/api/v1/experiments/{experiment_id}/spawn-positions",
            json={"seed": 2024},
        )
        assert second_spawn_response.status_code == 200
        assert second_spawn_response.json()["data"] == first_payload

        updated_allocation_response = client.put(
            f"/api/v1/experiments/{experiment_id}/crowd-allocation",
            json={
                "groups": [
                    {
                        "type_id": type_id,
                        "count": 2,
                        "distribution": {
                            "mode": "zone_fixed",
                            "fixed_zone_id": "entry_a",
                            "spread_radius": 2.5,
                            "avoid_exits": True,
                        },
                    }
                ],
                "agent_id_prefix": f"cache_{suffix[:4]}",
            },
        )
        assert updated_allocation_response.status_code == 409
        assert updated_allocation_response.json()["code"] == 2002

        updated_topology_response = client.put(
            f"/api/v1/experiments/{experiment_id}/topology",
            json={
                "zones": [
                    {
                        "zone_id": "entry_a",
                        "zone_name": "入口A",
                        "zone_type": "passage",
                        "bounds": {"min_x": 0, "min_z": 0, "max_x": 12, "max_z": 12},
                        "capacity": 100,
                        "is_entry_zone": True,
                        "metadata": {},
                    },
                    {
                        "zone_id": "entry_b",
                        "zone_name": "入口B",
                        "zone_type": "passage",
                        "bounds": {"min_x": 20, "min_z": 0, "max_x": 32, "max_z": 12},
                        "capacity": 100,
                        "is_entry_zone": True,
                        "metadata": {},
                    },
                ],
                "exits": [],
            },
        )
        assert updated_topology_response.status_code == 409
        assert updated_topology_response.json()["code"] == 2002
        assert len(fake_redis.store) == 1
    finally:
        _cleanup_pre_run_flow(experiment_id=experiment_id, type_id=type_id)


def test_create_experiment_can_replay_response_with_same_request_id(monkeypatch) -> None:
    client = TestClient(create_app())
    fake_redis = FakeRedisClient()
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(idempotency_api, "get_redis_client", lambda: fake_redis)
        monkeypatch.setattr(
            idempotency_api,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=True,
                request_idempotency_ttl_seconds=3600,
                request_idempotency_lock_ttl_seconds=30,
            ),
        )
        request_headers = {"X-Request-Id": f"create-experiment-{uuid4().hex[:8]}"}
        payload = {
            "name": f"幂等创建实验-{uuid4().hex[:8]}",
            "description": "用于验证 X-Request-Id 成功回放",
            "map_id": "demo-map",
            "llm_config": {
                "model": "gpt-4.1-mini",
                "temperature": 0.6,
                "max_tokens": 512,
                "timeout_ms": 5000,
            },
            "decision_config": {
                "batch_interval_ms": 1000,
                "batch_size": 20,
                "fallback_to_algorithm": True,
            },
            "tags": ["idempotency-test"],
        }

        first_response = client.post("/api/v1/experiments", json=payload, headers=request_headers)
        assert first_response.status_code == 201
        first_data = first_response.json()["data"]
        experiment_id = first_data["experiment_id"]

        second_response = client.post("/api/v1/experiments", json=payload, headers=request_headers)
        assert second_response.status_code == 201
        assert second_response.json()["data"] == first_data

        session = get_session_factory()()
        try:
            experiments = session.scalars(select(Experiment).where(Experiment.experiment_id == experiment_id)).all()
            assert len(experiments) == 1
        finally:
            session.close()
    finally:
        _cleanup_experiment_only(experiment_id=experiment_id)


def test_create_experiment_rejects_same_request_id_with_different_payload(monkeypatch) -> None:
    client = TestClient(create_app())
    fake_redis = FakeRedisClient()
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(idempotency_api, "get_redis_client", lambda: fake_redis)
        monkeypatch.setattr(
            idempotency_api,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=True,
                request_idempotency_ttl_seconds=3600,
                request_idempotency_lock_ttl_seconds=30,
            ),
        )
        request_headers = {"X-Request-Id": f"mismatch-experiment-{uuid4().hex[:8]}"}
        payload = {
            "name": f"幂等冲突实验-{uuid4().hex[:8]}",
            "description": "用于验证请求体摘要冲突",
            "map_id": "demo-map",
            "llm_config": {
                "model": "gpt-4.1-mini",
                "temperature": 0.6,
                "max_tokens": 512,
                "timeout_ms": 5000,
            },
            "decision_config": {
                "batch_interval_ms": 1000,
                "batch_size": 20,
                "fallback_to_algorithm": True,
            },
            "tags": ["idempotency-test"],
        }

        first_response = client.post("/api/v1/experiments", json=payload, headers=request_headers)
        assert first_response.status_code == 201
        experiment_id = first_response.json()["data"]["experiment_id"]

        changed_payload = deepcopy(payload)
        changed_payload["name"] = f"{payload['name']}-changed"
        second_response = client.post("/api/v1/experiments", json=changed_payload, headers=request_headers)
        assert second_response.status_code == 409
        second_payload = second_response.json()
        assert second_payload["code"] == 1003
        assert second_payload["data"]["reason"] == "idempotency_payload_mismatch"
        assert second_payload["data"]["request_id"] == request_headers["X-Request-Id"]
    finally:
        _cleanup_experiment_only(experiment_id=experiment_id)


def _cleanup_pre_run_flow(*, experiment_id: str | None, type_id: str) -> None:
    session = get_session_factory()()
    try:
        if experiment_id is not None:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            if experiment is not None:
                session.execute(delete(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id))
                session.execute(delete(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id))
                session.execute(delete(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id))
                session.execute(delete(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment.id))
                session.execute(delete(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment.id))
                session.execute(delete(ExperimentAllocation).where(ExperimentAllocation.experiment_id == experiment.id))
                session.execute(delete(ExperimentExit).where(ExperimentExit.experiment_id == experiment.id))
                session.execute(delete(ExperimentZone).where(ExperimentZone.experiment_id == experiment.id))
                session.execute(
                    delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
                )
                session.execute(delete(Experiment).where(Experiment.id == experiment.id))

        session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id == type_id))
        session.commit()
    finally:
        session.close()


def _cleanup_experiment_only(*, experiment_id: str | None) -> None:
    session = get_session_factory()()
    try:
        if experiment_id is None:
            return

        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
        if experiment is None:
            return

        session.execute(delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id))
        session.execute(delete(Experiment).where(Experiment.id == experiment.id))
        session.commit()
    finally:
        session.close()
