"""运行期主链路回归测试。"""

from __future__ import annotations

from copy import deepcopy
import threading
import time
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from backend.app.application.analytics import service as analytics_service
from backend.app.application.decisions import service as decisions_service
from backend.app.application.decisions.engine import (
    DecisionEngineCallFailed,
    DecisionEngineResponseParseFailed,
    DecisionEngineUnavailable,
)
from backend.app.infrastructure.db.models import (
    AgentDecision,
    CrowdTypeTemplate,
    DecisionMetric1m,
    DecisionBatch,
    ExitMetric1m,
    Experiment,
    ExperimentMaintenanceJob,
    ExperimentAgent,
    ExperimentAllocation,
    ExperimentArchive,
    ExperimentExit,
    ExperimentZone,
    HeatmapCache,
    TrajectoryBatch,
    TrajectoryPoint,
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
        return 0


class NoopRedisClient:
    """禁用热力图 Redis 热缓存影响的空实现。"""

    def get_json(self, *parts: object) -> dict[str, object] | None:
        return None

    def set_json(self, *parts: object, value: dict[str, object], ttl_seconds: int | None = None) -> bool:
        return True

    def delete_by_pattern(self, *parts: object) -> int:
        return 0


class SuccessfulFakeDecisionEngine:
    """用于测试的成功决策引擎。"""

    def __init__(self, *, chosen_exit_id: str, confidence: float = 0.88) -> None:
        self.chosen_exit_id = chosen_exit_id
        self.confidence = confidence

    def decide(self, agent, *, context, llm_timeout_ms: int, llm_model: str):
        del agent
        del context
        del llm_timeout_ms
        del llm_model
        return SimpleNamespace(
            chosen_exit_id=self.chosen_exit_id,
            reasoning="测试引擎返回的 LLM 推理结果",
            confidence=self.confidence,
        )


class FailingFakeDecisionEngine:
    """用于测试的失败决策引擎。"""

    def __init__(self, exception: Exception) -> None:
        self.exception = exception

    def decide(self, agent, *, context, llm_timeout_ms: int, llm_model: str):
        del agent
        del context
        del llm_timeout_ms
        del llm_model
        raise self.exception


class ConcurrentTrackingDecisionEngine:
    """用于验证决策批次是否启用了有限并发的测试引擎。"""

    def __init__(self, *, chosen_exit_id: str, sleep_seconds: float = 0.05) -> None:
        self.chosen_exit_id = chosen_exit_id
        self.sleep_seconds = sleep_seconds
        self._lock = threading.Lock()
        self.current_concurrency = 0
        self.max_concurrency = 0

    def decide(self, agent, *, context, llm_timeout_ms: int, llm_model: str):
        del agent
        del context
        del llm_timeout_ms
        del llm_model

        with self._lock:
            self.current_concurrency += 1
            self.max_concurrency = max(self.max_concurrency, self.current_concurrency)

        try:
            time.sleep(self.sleep_seconds)
            return SimpleNamespace(
                chosen_exit_id=self.chosen_exit_id,
                reasoning="并发测试引擎返回的 LLM 推理结果",
                confidence=0.91,
            )
        finally:
            with self._lock:
                self.current_concurrency -= 1


def test_decision_batch_can_persist_and_replay_idempotently(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_type_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: SuccessfulFakeDecisionEngine(chosen_exit_id="exit_1"),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        decision_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "入口区域拥挤，优先进入大厅",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.92,
                                "nl_description": "通往大厅 1，当前通行能力更高",
                            }
                        ],
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2400,
                        "patience_ratio": 0.08,
                        "algorithm_recommendation": None,
                        "dilemma_summary": "需要尽快离开入口区域",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.87,
                                "nl_description": "目前唯一可达出口",
                            }
                        ],
                    },
                ],
            },
        )
        assert decision_response.status_code == 200
        decision_payload = decision_response.json()["data"]
        assert decision_payload["batch_id"] == f"decision_batch_{suffix}"
        assert decision_payload["total_count"] == 2
        assert decision_payload["success_count"] == 2
        assert decision_payload["fallback_count"] == 0
        assert all(item["decision_source"] == "llm" for item in decision_payload["decisions"])
        assert all(item["chosen_exit_id"] == "exit_1" for item in decision_payload["decisions"])

        replay_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 9999,
                        "patience_ratio": 0.99,
                        "algorithm_recommendation": None,
                        "dilemma_summary": "重复提交应命中幂等缓存",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.1,
                                "nl_description": "这份数据不该被重新写入",
                            }
                        ],
                    }
                ],
            },
        )
        assert replay_response.status_code == 200
        assert replay_response.json()["data"] == decision_payload

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            decision_batches = session.scalars(
                select(DecisionBatch).where(DecisionBatch.experiment_id == experiment.id)
            ).all()
            agent_decisions = session.scalars(
                select(AgentDecision).where(AgentDecision.experiment_id == experiment.id)
            ).all()
            decision_metrics = session.scalars(
                select(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment.id)
            ).all()
            assert len(decision_batches) == 1
            assert len(agent_decisions) == 2
            assert len(decision_metrics) == 2
        finally:
            session.close()

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 45000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200
        summary = complete_response.json()["data"]["summary"]
        assert summary["total_decision_requests"] == 1
        assert summary["total_llm_decisions"] == 2
        assert summary["llm_fallback_count"] == 0
        assert summary["crowd_type_stats"] == [
            {
                "type_id": type_id,
                "final_count": 2,
                "exited_count": 0,
            }
        ]
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_redis_lock_can_block_inflight_batch(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_lock_{suffix}"
    experiment_id: str | None = None
    fake_redis = FakeRedisClient()

    try:
        monkeypatch.setattr(decisions_service, "get_redis_client", lambda: fake_redis)
        monkeypatch.setattr(
            decisions_service,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=True,
                decision_batch_lock_ttl_seconds=30,
                decision_experiment_lock_ttl_seconds=30,
                decision_batch_hard_timeout_seconds=8.0,
            ),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            locked = fake_redis.set_if_absent(
                "decisions",
                "batch-lock",
                "exp",
                str(experiment.id),
                "batch:locked_batch",
                value="other-request",
                ttl_seconds=30,
            )
            assert locked is True
        finally:
            session.close()

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": "locked_batch",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于测试 Redis 批次锁冲突",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.92,
                                "nl_description": "当前唯一出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == 2002
        assert payload["data"]["batch_id"] == "locked_batch"
        assert payload["data"]["reason"] == "decision_batch_processing"
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_experiment_lock_can_block_other_batches(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_exp_lock_{suffix}"
    experiment_id: str | None = None
    fake_redis = FakeRedisClient()

    try:
        monkeypatch.setattr(decisions_service, "get_redis_client", lambda: fake_redis)
        monkeypatch.setattr(
            decisions_service,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=True,
                decision_batch_lock_ttl_seconds=30,
                decision_experiment_lock_ttl_seconds=30,
                decision_batch_hard_timeout_seconds=8.0,
            ),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=1,
        )

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            locked = fake_redis.set_if_absent(
                "decisions",
                "experiment-lock",
                "exp",
                str(experiment.id),
                value="other-request",
                ttl_seconds=30,
            )
            assert locked is True
        finally:
            session.close()

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"another_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证实验级锁冲突",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.92,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 409
        payload = response.json()
        assert payload["code"] == 2002
        assert payload["data"]["reason"] == "decision_request_inflight"
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_can_fallback_when_llm_call_fails(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_fallback_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: FailingFakeDecisionEngine(DecisionEngineCallFailed("模拟 LLM 调用失败")),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=1,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"fallback_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证 fallback 分支",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["success_count"] == 0
        assert payload["fallback_count"] == 1
        assert payload["decisions"][0]["decision_source"] == "fallback"
        assert payload["decisions"][0]["chosen_exit_id"] == "exit_1"
        assert payload["decisions"][0]["reasoning"] is None
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_can_return_none_when_llm_fails_without_fallback(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_none_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: FailingFakeDecisionEngine(DecisionEngineCallFailed("模拟 LLM 调用失败")),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=False,
            agent_count=1,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"none_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证无兜底空结果分支",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["success_count"] == 0
        assert payload["fallback_count"] == 0
        assert payload["decisions"][0]["decision_source"] == "none"
        assert payload["decisions"][0]["chosen_exit_id"] is None
        assert payload["decisions"][0]["reasoning"] is None
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


@pytest.mark.parametrize(
    ("fallback_to_algorithm", "expected_source", "expected_fallback_count", "expected_chosen_exit_id"),
    [
        (True, "fallback", 1, "exit_1"),
        (False, "none", 0, None),
    ],
)
def test_decision_batch_absorbs_parse_failures_into_batch_results(
    monkeypatch,
    fallback_to_algorithm: bool,
    expected_source: str,
    expected_fallback_count: int,
    expected_chosen_exit_id: str | None,
) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_parse_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: FailingFakeDecisionEngine(DecisionEngineResponseParseFailed("模拟 LLM 返回解析失败")),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=1,
            fallback_to_algorithm=fallback_to_algorithm,
            agent_count=1,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"parse_failure_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证解析失败按批内结果收口",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["code"] == 0
        payload = envelope["data"]
        assert payload["success_count"] == 0
        assert payload["fallback_count"] == expected_fallback_count
        assert payload["decisions"][0]["decision_source"] == expected_source
        assert payload["decisions"][0]["chosen_exit_id"] == expected_chosen_exit_id
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


@pytest.mark.parametrize(
    ("fallback_to_algorithm", "expected_second_source", "expected_fallback_count", "expected_second_exit_id"),
    [
        (True, "fallback", 1, "exit_1"),
        (False, "none", 0, None),
    ],
)
def test_decision_batch_absorbs_hard_timeout_into_batch_results(
    monkeypatch,
    fallback_to_algorithm: bool,
    expected_second_source: str,
    expected_fallback_count: int,
    expected_second_exit_id: str | None,
) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_timeout_{suffix}"
    experiment_id: str | None = None
    perf_values = iter([100.0, 100.0, 100.0, 100.001, 100.75, 100.75])

    def fake_perf_counter() -> float:
        return next(perf_values, 100.75)

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: SuccessfulFakeDecisionEngine(chosen_exit_id="exit_1"),
        )
        monkeypatch.setattr(decisions_service, "perf_counter", fake_perf_counter)
        monkeypatch.setattr(
            decisions_service,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=False,
                decision_batch_lock_ttl_seconds=0,
                decision_experiment_lock_ttl_seconds=0,
                decision_batch_hard_timeout_seconds=0.5,
                decision_batch_max_concurrency=1,
            ),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=fallback_to_algorithm,
            agent_count=2,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"timeout_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[index]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000 + (index * 200),
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": f"用于验证批次硬超时，第 {index + 1} 个 agent",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                    for index in range(2)
                ],
            },
        )

        assert response.status_code == 200
        envelope = response.json()
        assert envelope["code"] == 0
        payload = envelope["data"]
        assert payload["success_count"] == 1
        assert payload["fallback_count"] == expected_fallback_count
        assert payload["decisions"][0]["decision_source"] == "llm"
        assert payload["decisions"][0]["chosen_exit_id"] == "exit_1"
        assert payload["decisions"][1]["decision_source"] == expected_second_source
        assert payload["decisions"][1]["chosen_exit_id"] == expected_second_exit_id
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_can_use_limited_concurrency(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_concurrency_{suffix}"
    experiment_id: str | None = None
    tracking_engine = ConcurrentTrackingDecisionEngine(chosen_exit_id="exit_1")

    try:
        monkeypatch.setattr(decisions_service, "get_decision_engine", lambda: tracking_engine)
        monkeypatch.setattr(
            decisions_service,
            "get_settings",
            lambda: SimpleNamespace(
                redis_enabled=False,
                decision_batch_lock_ttl_seconds=0,
                decision_experiment_lock_ttl_seconds=0,
                decision_batch_hard_timeout_seconds=8.0,
                decision_batch_max_concurrency=4,
            ),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=4,
            fallback_to_algorithm=True,
            agent_count=4,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"concurrency_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[index]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000 + (index * 200),
                        "patience_ratio": 0.15 + (index * 0.05),
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": f"用于验证有限并发，第 {index + 1} 个 agent",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.92,
                                "nl_description": "当前唯一出口",
                            }
                        ],
                    }
                    for index in range(4)
                ],
            },
        )

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["success_count"] == 4
        assert tracking_engine.max_concurrency > 1
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_returns_3004_when_engine_unavailable_and_fallback_disabled(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_unavailable_{suffix}"
    experiment_id: str | None = None

    try:
        def raise_unavailable():
            raise DecisionEngineUnavailable("当前未配置可用的 LLM 决策引擎")

        monkeypatch.setattr(decisions_service, "get_decision_engine", raise_unavailable)
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=False,
            agent_count=1,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"unavailable_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 3000,
                        "patience_ratio": 0.15,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证 3004 错误",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一可选出口",
                            }
                        ],
                    }
                ],
            },
        )

        assert response.status_code == 503
        payload = response.json()
        assert payload["code"] == 3004
        assert payload["data"]["reason"] == "llm_service_unavailable"
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_decision_batch_respects_configured_batch_size() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"decision_limit_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=1,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"decision_limit_batch_{suffix}",
                "simulation_time_ms": 800,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2000,
                        "patience_ratio": 0.1,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "超过批次上限测试 1",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.8,
                                "nl_description": "唯一出口",
                            }
                        ],
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2100,
                        "patience_ratio": 0.12,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "超过批次上限测试 2",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.82,
                                "nl_description": "唯一出口",
                            }
                        ],
                    },
                ],
            },
        )
        assert response.status_code == 400
        payload = response.json()
        assert payload["code"] == 1005
        assert payload["data"]["limit"] == 1
        assert payload["data"]["requested_count"] == 2
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_trajectory_batch_can_deduplicate_and_update_completion_summary() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"trajectory_type_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        first_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"trajectory_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.2345, "z": 2.3456},
                        "zone_id": "entry_a",
                        "speed": 1.26,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.8, "z": 2.9},
                        "zone_id": "entry_a",
                        "speed": 1.18,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 3.0, "z": 4.0},
                        "zone_id": "entry_a",
                        "speed": 0.95,
                        "state": "queuing",
                        "target_exit_id": "exit_1",
                        "is_queuing": True,
                    },
                ],
            },
        )
        assert first_response.status_code == 200
        first_payload = first_response.json()["data"]
        assert first_payload["accepted_count"] == 2
        assert first_payload["rejected_count"] == 1
        assert first_payload["total_stored_points"] == 2

        replay_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"trajectory_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 99.0, "z": 88.0},
                        "zone_id": "hall_1",
                        "speed": 9.99,
                        "state": "idle",
                        "target_exit_id": None,
                        "is_queuing": False,
                    }
                ],
            },
        )
        assert replay_response.status_code == 200
        assert replay_response.json()["data"]["batch_id"] == first_payload["batch_id"]
        assert replay_response.json()["data"]["accepted_count"] == first_payload["accepted_count"]
        assert replay_response.json()["data"]["rejected_count"] == first_payload["rejected_count"]

        second_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"trajectory_batch_{suffix}_next",
                "simulation_time_ms": 1200,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 5.0, "z": 6.0},
                        "zone_id": "hall_1",
                        "speed": 1.05,
                        "state": "transitioning",
                        "target_exit_id": None,
                        "is_queuing": False,
                    }
                ],
            },
        )
        assert second_response.status_code == 200
        assert second_response.json()["data"]["accepted_count"] == 1
        assert second_response.json()["data"]["rejected_count"] == 0
        assert second_response.json()["data"]["total_stored_points"] == 3

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            trajectory_batches = session.scalars(
                select(TrajectoryBatch).where(TrajectoryBatch.experiment_id == experiment.id)
            ).all()
            trajectory_points = session.scalars(
                select(TrajectoryPoint).where(TrajectoryPoint.experiment_id == experiment.id)
            ).all()
            zone_metrics = session.scalars(
                select(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id)
            ).all()
            exit_metrics = session.scalars(
                select(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id)
            ).all()
            assert len(trajectory_batches) == 2
            assert len(trajectory_points) == 3
            first_agent_point = next(
                point
                for point in trajectory_points
                if point.agent_id == spawned_agents[0]["agent_id"] and point.simulation_time_ms == 1000
            )
            assert float(first_agent_point.position_x) == 1.235
            assert float(first_agent_point.position_z) == 2.346
            assert zone_metrics == []
            assert exit_metrics == []
        finally:
            session.close()

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 47000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200
        summary = complete_response.json()["data"]["summary"]
        assert summary["total_trajectory_points"] == 3
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_analytics_endpoints_can_query_runtime_data(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"analytics_type_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(analytics_service, "get_redis_client", lambda: NoopRedisClient())
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: SuccessfulFakeDecisionEngine(chosen_exit_id="exit_1"),
        )
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=4,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        decision_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"analytics_decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2800,
                        "patience_ratio": 0.18,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "入口区域较拥挤，需要尽快前往大厅",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.91,
                                "nl_description": "通往大厅 1，当前通行更稳定",
                            }
                        ],
                    }
                ],
            },
        )
        assert decision_response.status_code == 200

        first_trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"analytics_trajectory_batch_{suffix}_1",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.2, "z": 2.3},
                        "zone_id": "entry_a",
                        "speed": 1.24,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.0, "z": 2.8},
                        "zone_id": "entry_a",
                        "speed": 0.86,
                        "state": "queuing",
                        "target_exit_id": "exit_1",
                        "is_queuing": True,
                    },
                ],
            },
        )
        assert first_trajectory_response.status_code == 200

        second_trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"analytics_trajectory_batch_{suffix}_2",
                "simulation_time_ms": 1200,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 13.4, "z": 6.2},
                        "zone_id": "hall_1",
                        "speed": 1.07,
                        "state": "transitioning",
                        "target_exit_id": None,
                        "is_queuing": False,
                    }
                ],
            },
        )
        assert second_trajectory_response.status_code == 200

        stats_response = client.get(f"/api/v1/experiments/{experiment_id}/stats")
        assert stats_response.status_code == 200
        stats_payload = stats_response.json()["data"]
        assert stats_payload["experiment_id"] == experiment_id
        assert stats_payload["allocation_snapshot"]["total_count"] == 2
        assert stats_payload["time_range"]["start_ms"] == 1000
        assert stats_payload["time_range"]["end_ms"] == 1200
        assert stats_payload["agent_stats"]["total_count"] == 2
        assert stats_payload["decision_stats"]["total_batches"] == 1
        assert stats_payload["decision_stats"]["total_agents_decided"] == 1
        assert len(stats_payload["zone_stats"]) >= 1
        assert len(stats_payload["exit_stats"]) == 1
        assert stats_payload["zone_stats"][0]["peak_time_ms"] in {0, 1000}
        assert 0.0 <= stats_payload["zone_stats"][0]["utilization_rate"] <= 1.0

        trajectories_records_response = client.get(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            params={"page": 1, "page_size": 10, "format": "records"},
        )
        assert trajectories_records_response.status_code == 200
        records_payload = trajectories_records_response.json()["data"]
        assert records_payload["total_count"] == 3
        assert len(records_payload["records"]) == 3

        trajectories_compact_response = client.get(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            params={"page": 1, "page_size": 10, "format": "compact"},
        )
        assert trajectories_compact_response.status_code == 200
        compact_payload = trajectories_compact_response.json()["data"]
        assert compact_payload["total_count"] == 3
        assert len(compact_payload["agents"]) == 2

        detail_response = client.get(
            f"/api/v1/experiments/{experiment_id}/trajectories/{spawned_agents[0]['agent_id']}",
            params={"include_decisions": "true"},
        )
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()["data"]
        assert detail_payload["agent_id"] == spawned_agents[0]["agent_id"]
        assert detail_payload["point_count"] == 2
        assert detail_payload["time_span_ms"] == 200
        assert len(detail_payload["decisions"]) == 1
        assert detail_payload["decisions"][0]["batch_id"] == f"analytics_decision_batch_{suffix}"

        heatmap_request = {
            "time_start_ms": 1000,
            "time_end_ms": 1200,
            "grid_config": {
                "origin": {"x": 0.0, "z": 0.0},
                "cell_size": 10.0,
                "cols": 4,
                "rows": 4,
            },
            "metric": "passage_count",
            "crowd_type_filter": type_id,
            "zone_id_filter": None,
            "normalization": "min_max",
        }
        heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=heatmap_request,
        )
        assert heatmap_response.status_code == 200
        heatmap_payload = heatmap_response.json()["data"]
        assert heatmap_payload["grid_config"]["cols"] == 4
        assert len(heatmap_payload["cells"]) == 16
        assert heatmap_payload["statistics"]["total_cells"] == 16

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            cache_rows = session.scalars(
                select(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id)
            ).all()
            assert len(cache_rows) == 1
            cache_rows[0].statistics_json = {
                "min_value": 0.0,
                "max_value": 0.777,
                "raw_min": 0.0,
                "raw_max": 7.0,
                "percentile_99_raw": None,
                "non_empty_cells": 1,
                "total_cells": 16,
            }
            cache_rows[0].cells = [0.777] + [0.0] * 15
            session.commit()
        finally:
            session.close()

        cached_heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=heatmap_request,
        )
        assert cached_heatmap_response.status_code == 200
        cached_heatmap_payload = cached_heatmap_response.json()["data"]
        assert cached_heatmap_payload["cells"][0] == 0.777
        assert cached_heatmap_payload["statistics"]["non_empty_cells"] == 1

        compare_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap/compare",
            json={
                "time_start_ms": heatmap_request["time_start_ms"],
                "time_end_ms": heatmap_request["time_end_ms"],
                "grid_config": heatmap_request["grid_config"],
                "metric": heatmap_request["metric"],
                "crowd_types": [type_id],
                "normalization": heatmap_request["normalization"],
            },
        )
        assert compare_response.status_code == 200
        compare_payload = compare_response.json()["data"]
        assert len(compare_payload["layers"]) == 1
        assert compare_payload["layers"][0]["crowd_type"] == type_id
        assert len(compare_payload["layers"][0]["cells"]) == 16
        assert compare_payload["layers"][0]["cells"][0] == 0.777

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            cache_rows = session.scalars(
                select(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id)
            ).all()
            assert len(cache_rows) == 1
        finally:
            session.close()

        path_traces_response = client.get(
            f"/api/v1/experiments/{experiment_id}/path-traces",
            params={"crowd_type": type_id, "sample_rate": 1, "max_agents": 10},
        )
        assert path_traces_response.status_code == 200
        path_traces_payload = path_traces_response.json()["data"]
        assert path_traces_payload["crowd_type"] == type_id
        assert path_traces_payload["total_agents_of_type"] == 2
        assert path_traces_payload["agent_count_in_response"] == 2
        assert len(path_traces_payload["traces"]) == 2
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_heatmap_can_use_redis_hot_cache_when_enabled(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"redis_heatmap_{suffix}"
    experiment_id: str | None = None
    fake_redis = FakeRedisClient()

    try:
        monkeypatch.setattr(analytics_service, "get_redis_client", lambda: fake_redis)
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"redis_heatmap_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.2, "z": 2.3},
                        "zone_id": "entry_a",
                        "speed": 1.24,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.0, "z": 2.8},
                        "zone_id": "entry_a",
                        "speed": 0.86,
                        "state": "queuing",
                        "target_exit_id": "exit_1",
                        "is_queuing": True,
                    },
                ],
            },
        )
        assert trajectory_response.status_code == 200

        request_payload = {
            "time_start_ms": 1000,
            "time_end_ms": 1000,
            "grid_config": {
                "origin": {"x": 0.0, "z": 0.0},
                "cell_size": 10.0,
                "cols": 4,
                "rows": 4,
            },
            "metric": "passage_count",
            "crowd_type_filter": type_id,
            "zone_id_filter": None,
            "normalization": "min_max",
        }
        first_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=request_payload,
        )
        assert first_response.status_code == 200
        assert len(fake_redis.store) == 1

        redis_key = next(iter(fake_redis.store))
        redis_payload = deepcopy(fake_redis.store[redis_key])
        assert isinstance(redis_payload, dict)
        redis_payload["cells"][0] = 0.555
        redis_payload["statistics"]["non_empty_cells"] = 1
        fake_redis.store[redis_key] = redis_payload

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            session.execute(delete(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id))
            session.commit()
        finally:
            session.close()

        second_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=request_payload,
        )
        assert second_response.status_code == 200
        second_payload = second_response.json()["data"]
        assert second_payload["cells"][0] == 0.555
        assert second_payload["statistics"]["non_empty_cells"] == 1
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_complete_summary_can_include_crowd_type_exit_stats() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"completion_type_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
            hall_zone_type="exit_final",
        )

        first_trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"completion_traj_1_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.5, "z": 2.1},
                        "zone_id": "entry_a",
                        "speed": 1.05,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.0, "z": 2.4},
                        "zone_id": "entry_a",
                        "speed": 0.98,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                ],
            },
        )
        assert first_trajectory_response.status_code == 200

        second_trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"completion_traj_2_{suffix}",
                "simulation_time_ms": 1200,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 18.0, "z": 8.5},
                        "zone_id": "hall_1",
                        "speed": 1.10,
                        "state": "transitioning",
                        "target_exit_id": None,
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.8, "z": 3.1},
                        "zone_id": "entry_a",
                        "speed": 0.90,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                ],
            },
        )
        assert second_trajectory_response.status_code == 200

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 30000,
                "final_agent_count": 1,
            },
        )
        assert complete_response.status_code == 200
        summary = complete_response.json()["data"]["summary"]
        assert summary["crowd_type_stats"] == [
            {
                "type_id": type_id,
                "final_count": 1,
                "exited_count": 1,
            }
        ]
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_stats_can_clamp_zone_utilization_when_peak_count_exceeds_capacity() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"over_capacity_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
            entry_capacity=1,
        )

        trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"over_capacity_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.0, "z": 2.0},
                        "zone_id": "entry_a",
                        "speed": 1.1,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.0, "z": 3.0},
                        "zone_id": "entry_a",
                        "speed": 0.9,
                        "state": "queuing",
                        "target_exit_id": "exit_1",
                        "is_queuing": True,
                    },
                ],
            },
        )
        assert trajectory_response.status_code == 200

        stats_response = client.get(f"/api/v1/experiments/{experiment_id}/stats")
        assert stats_response.status_code == 200
        zone_stats = stats_response.json()["data"]["zone_stats"]
        entry_zone = next(item for item in zone_stats if item["zone_id"] == "entry_a")
        assert entry_zone["peak_count"] == 2
        assert entry_zone["utilization_rate"] == 1.0
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def _prepare_running_experiment(
    client: TestClient,
    *,
    suffix: str,
    type_id: str,
    batch_size: int,
    fallback_to_algorithm: bool,
    agent_count: int,
    hall_zone_type: str = "plaza",
    entry_capacity: int = 100,
) -> tuple[str, list[dict[str, Any]]]:
    create_template_response = client.post(
        "/api/v1/crowd-types",
        json={
            "type_id": type_id,
            "display_name": "决策联调模板",
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
            "name": f"决策测试-{suffix}",
            "description": "用于回归决策批次",
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
            "tags": ["decision-test"],
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
            "agent_id_prefix": f"dec_{suffix[:4]}",
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


def _cleanup_runtime_flow(*, experiment_id: str | None, type_id: str) -> None:
    session = get_session_factory()()
    try:
        if experiment_id is not None:
            session.execute(delete(ExperimentArchive).where(ExperimentArchive.experiment_id == experiment_id))
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            if experiment is not None:
                session.execute(delete(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id))
                session.execute(delete(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id))
                session.execute(delete(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id))
                session.execute(delete(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment.id))
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

        session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id == type_id))
        session.commit()
    finally:
        session.close()
