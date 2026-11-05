"""RQ 维护任务适配层回归测试。"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from backend.app.api.errors import AppError
from backend.app.core.context import get_experiment_id, get_request_id
from backend.app.domain.enums import ExperimentStatus, MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.models import (
    AgentMetric1m,
    Experiment,
    ExperimentMaintenanceJob,
    ExitMetric1m,
    TrajectoryPointSampled1s,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.workers import rq_worker
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.unit.test_decision_flow import _cleanup_runtime_flow, _prepare_running_experiment


class _FakeRQJob:
    """伪造的 RQ job。"""

    def __init__(self, job_id: str) -> None:
        self.id = job_id


class _FakeQueue:
    """用于测试派发逻辑的伪造队列。"""

    def __init__(self, name: str = "maintenance") -> None:
        self.name = name
        self.connection = object()
        self.enqueued: list[tuple[str, dict[str, object]]] = []
        self.scheduled: list[tuple[float, str, dict[str, object]]] = []

    def enqueue(self, func: str, **kwargs: object) -> _FakeRQJob:
        self.enqueued.append((func, dict(kwargs)))
        return _FakeRQJob(f"immediate-{len(self.enqueued)}")

    def enqueue_in(self, delay: timedelta, func: str, **kwargs: object) -> _FakeRQJob:
        self.scheduled.append((delay.total_seconds(), func, dict(kwargs)))
        return _FakeRQJob(f"scheduled-{len(self.scheduled)}")


def test_dispatch_pending_maintenance_jobs_can_enqueue_due_jobs_to_rq(monkeypatch) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(ExperimentMaintenanceJob))
        session.commit()
    finally:
        session.close()

    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"rq_dispatch_type_{suffix}"
    experiment_id: str | None = None
    queue = _FakeQueue()
    settings = replace(
        rq_worker.get_settings(),
        maintenance_rq_ttl_seconds=1234,
        maintenance_rq_result_ttl_seconds=2345,
        maintenance_rq_failure_ttl_seconds=3456,
    )

    try:
        experiment_id, _ = _prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 12000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200

        monkeypatch.setattr(rq_worker, "_get_maintenance_queue", lambda required=False: queue)
        monkeypatch.setattr(rq_worker, "get_settings", lambda: settings)

        result = rq_worker.dispatch_pending_maintenance_jobs(limit=20, fanout=2)
        assert result.status == "enqueued"
        assert result.queue_name == "maintenance"
        assert result.due_job_count >= 1
        assert result.enqueued_count >= 1
        assert len(result.rq_job_ids) == result.enqueued_count
        assert len(queue.enqueued) == result.enqueued_count
        for func, kwargs in queue.enqueued:
            assert func == "backend.app.infrastructure.workers.rq_worker.process_maintenance_dispatch"
            assert kwargs["job_timeout"] == settings.maintenance_rq_job_timeout_seconds
            assert kwargs["ttl"] == settings.maintenance_rq_ttl_seconds
            assert kwargs["result_ttl"] == settings.maintenance_rq_result_ttl_seconds
            assert kwargs["failure_ttl"] == settings.maintenance_rq_failure_ttl_seconds
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_enqueue_trajectory_refresh_job_can_enqueue_direct_rq_job(monkeypatch) -> None:
    queue = _FakeQueue()
    settings = replace(
        rq_worker.get_settings(),
        maintenance_rq_ttl_seconds=111,
        maintenance_rq_result_ttl_seconds=222,
        maintenance_rq_failure_ttl_seconds=333,
    )

    monkeypatch.setattr(rq_worker, "_get_maintenance_queue", lambda required=False: queue)
    monkeypatch.setattr(rq_worker, "get_settings", lambda: settings)

    result = rq_worker.enqueue_trajectory_refresh_job(
        experiment_id="exp-traj-refresh",
        batch_id="batch-001",
        simulation_time_ms_values=[1200],
        agent_ids=["agent-1", "agent-2"],
    )

    assert result.status == "enqueued"
    assert result.queue_name == "maintenance"
    assert result.rq_job_id is not None
    assert len(queue.enqueued) == 1
    func, kwargs = queue.enqueued[0]
    assert func == "backend.app.infrastructure.workers.rq_worker.process_trajectory_refresh_job"
    assert kwargs["experiment_id"] == "exp-traj-refresh"
    assert kwargs["simulation_time_ms_values"] == [1200]
    assert kwargs["agent_ids"] == ["agent-1", "agent-2"]
    assert kwargs["job_timeout"] == settings.maintenance_rq_job_timeout_seconds
    assert kwargs["ttl"] == settings.maintenance_rq_ttl_seconds
    assert kwargs["result_ttl"] == settings.maintenance_rq_result_ttl_seconds
    assert kwargs["failure_ttl"] == settings.maintenance_rq_failure_ttl_seconds


def test_dispatch_pending_maintenance_jobs_can_schedule_delayed_retry(monkeypatch) -> None:
    session = get_session_factory()()
    queue = _FakeQueue()
    settings = replace(
        rq_worker.get_settings(),
        maintenance_rq_ttl_seconds=4321,
        maintenance_rq_result_ttl_seconds=5432,
        maintenance_rq_failure_ttl_seconds=6543,
    )
    experiment_id = f"rq_delay_exp_{uuid4().hex[:8]}"
    experiment: Experiment | None = None
    try:
        experiment = Experiment(
            experiment_id=experiment_id,
            name="RQ 延迟派发测试",
            description="用于验证未来任务会转成延迟调度",
            map_id="delay-map",
            status=ExperimentStatus.COMPLETED,
            llm_model="gpt-4.1-mini",
            llm_temperature=0.6,
            llm_max_tokens=256,
            llm_timeout_ms=5000,
            decision_batch_interval_ms=1000,
            decision_batch_size=10,
            fallback_to_algorithm=True,
            tags=[],
        )
        session.add(experiment)
        session.flush()
        session.add_all(
            [
                ExperimentMaintenanceJob(
                    experiment_id=experiment.id,
                    job_type=MaintenanceJobType.ANALYTICS_REFRESH,
                    status=MaintenanceJobStatus.PENDING,
                    payload_json={},
                    result_json={},
                    scheduled_at=datetime.now(timezone.utc) + timedelta(seconds=45),
                ),
                ExperimentMaintenanceJob(
                    experiment_id=experiment.id,
                    job_type=MaintenanceJobType.HEATMAP_PREWARM,
                    status=MaintenanceJobStatus.PENDING,
                    payload_json={},
                    result_json={},
                ),
            ]
        )
        session.commit()

        monkeypatch.setattr(rq_worker, "_get_maintenance_queue", lambda required=False: queue)
        monkeypatch.setattr(rq_worker, "get_settings", lambda: settings)

        result = rq_worker.dispatch_pending_maintenance_jobs(limit=20, fanout=1)
        assert result.status == "scheduled"
        assert result.enqueued_count == 1
        assert result.next_scheduled_delay_seconds is not None
        assert result.next_scheduled_delay_seconds > 0
        assert len(queue.scheduled) == 1
        assert queue.scheduled[0][0] > 0
        assert queue.scheduled[0][1] == "backend.app.infrastructure.workers.rq_worker.process_maintenance_dispatch"
        assert queue.scheduled[0][2]["job_timeout"] == settings.maintenance_rq_job_timeout_seconds
        assert queue.scheduled[0][2]["ttl"] == settings.maintenance_rq_ttl_seconds
        assert queue.scheduled[0][2]["result_ttl"] == settings.maintenance_rq_result_ttl_seconds
        assert queue.scheduled[0][2]["failure_ttl"] == settings.maintenance_rq_failure_ttl_seconds
    finally:
        if experiment is not None:
            session.execute(delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id))
            session.execute(delete(Experiment).where(Experiment.id == experiment.id))
            session.commit()
        session.close()


def test_run_maintenance_rq_worker_fails_fast_when_redis_precheck_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        rq_worker,
        "check_database_health",
        lambda: {
            "backend": "postgresql",
            "connected": True,
            "status": "ok",
            "database": "crowd_flow_simulation_local",
            "message": "数据库连接正常",
        },
    )
    monkeypatch.setattr(
        rq_worker,
        "get_redis_client",
        lambda: SimpleNamespace(
            healthcheck=lambda: {
                "backend": "redis",
                "enabled": True,
                "connected": False,
                "status": "error",
                "message": "Redis 连接失败: mock",
            },
            get_connection=lambda: None,
        ),
    )

    with pytest.raises(AppError) as exc_info:
        rq_worker.run_maintenance_rq_worker(burst=True, max_jobs=1)

    assert exc_info.value.code == 4002


def test_run_maintenance_rq_worker_uses_simple_worker(monkeypatch) -> None:
    fake_queue = _FakeQueue()
    worker_calls: dict[str, object] = {}

    class _FakeSimpleWorker:
        def __init__(self, queues: list[object], connection: object, name: str | None = None) -> None:
            self.queues = queues
            self.connection = connection
            self.name = name or "fake-rq-worker"

        def work(
            self,
            *,
            burst: bool = False,
            logging_level: str | None = None,
            date_format: str = "%H:%M:%S",
            log_format: str = "%(asctime)s %(message)s",
            max_jobs: int | None = None,
            max_idle_time: int | None = None,
            with_scheduler: bool = False,
            dequeue_strategy: object | None = None,
        ) -> bool:
            worker_calls.update(
                {
                    "burst": burst,
                    "logging_level": logging_level,
                    "max_jobs": max_jobs,
                    "with_scheduler": with_scheduler,
                    "date_format": date_format,
                    "log_format": log_format,
                    "max_idle_time": max_idle_time,
                    "dequeue_strategy": dequeue_strategy,
                }
            )
            return True

    monkeypatch.setattr(
        rq_worker,
        "check_database_health",
        lambda: {
            "backend": "postgresql",
            "connected": True,
            "status": "ok",
            "database": "crowd_flow_simulation_local",
            "message": "数据库连接正常",
        },
    )
    monkeypatch.setattr(
        rq_worker,
        "get_redis_client",
        lambda: SimpleNamespace(
            healthcheck=lambda: {
                "backend": "redis",
                "enabled": True,
                "connected": True,
                "status": "ok",
                "message": "Redis 连接正常",
            },
            get_connection=lambda: object(),
        ),
    )
    monkeypatch.setattr(rq_worker, "_get_maintenance_queue", lambda required=True: fake_queue)
    monkeypatch.setattr(rq_worker, "_import_rq_components", lambda: (object, _FakeSimpleWorker))

    result = rq_worker.run_maintenance_rq_worker(
        burst=True,
        max_jobs=2,
        with_scheduler=False,
        worker_name="test-rq-worker",
    )

    assert result["backend"] == "rq"
    assert result["queue_name"] == "maintenance"
    assert result["worker_name"] == "test-rq-worker"
    assert result["burst"] is True
    assert result["max_jobs"] == 2
    assert result["with_scheduler"] is False
    assert result["work_result"] is True
    assert worker_calls["burst"] is True
    assert worker_calls["max_jobs"] == 2
    assert worker_calls["with_scheduler"] is False


def test_get_maintenance_queue_uses_binary_safe_rq_connection(monkeypatch) -> None:
    binary_connection = object()
    created_queues: list[tuple[str, object]] = []

    class _FakeQueueClass:
        def __init__(self, name: str, connection: object) -> None:
            created_queues.append((name, connection))
            self.name = name
            self.connection = connection

    monkeypatch.setattr(
        rq_worker,
        "get_redis_client",
        lambda: SimpleNamespace(
            get_rq_connection=lambda: binary_connection,
            get_connection=lambda: object(),
        ),
    )
    monkeypatch.setattr(rq_worker, "_import_rq_components", lambda: (_FakeQueueClass, object))

    queue = rq_worker._get_maintenance_queue(required=True)

    assert queue is not None
    assert queue.connection is binary_connection
    assert created_queues == [(rq_worker.get_settings().maintenance_queue_name, binary_connection)]


def test_context_aware_simple_worker_injects_rq_job_context(monkeypatch) -> None:
    import rq.worker

    _, worker_class = rq_worker._import_rq_components()
    observed: dict[str, str | None] = {}

    def fake_execute_job(self, job, queue):
        del self, queue
        observed["request_id"] = get_request_id()
        observed["experiment_id"] = get_experiment_id()
        return {"job_id": job.id}

    monkeypatch.setattr(rq.worker.SimpleWorker, "execute_job", fake_execute_job)

    worker = worker_class.__new__(worker_class)
    job = SimpleNamespace(id="rq-test-job", kwargs={"experiment_id": "exp_ctx_001"})

    result = worker.execute_job(job, object())

    assert result == {"job_id": "rq-test-job"}
    assert observed["request_id"] == "rq-job-rq-test-job"
    assert observed["experiment_id"] == "exp_ctx_001"
    assert get_request_id() == "-"
    assert get_experiment_id() is None


def test_process_trajectory_refresh_job_can_materialize_derived_tables() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"rq_traj_refresh_{suffix}"
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

        trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"rq_refresh_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.2, "z": 2.4},
                        "zone_id": "entry_a",
                        "speed": 1.0,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.8, "z": 2.8},
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

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            assert session.scalars(select(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(AgentMetric1m).where(AgentMetric1m.experiment_id == experiment.id)).all() == []
            assert (
                session.scalars(
                    select(TrajectoryPointSampled1s).where(TrajectoryPointSampled1s.experiment_id == experiment.id)
                ).all()
                == []
            )
        finally:
            session.close()

        refresh_payload = rq_worker.process_trajectory_refresh_job(
            experiment_id=experiment_id,
            simulation_time_ms_values=[1000],
            agent_ids=[spawned_agents[0]["agent_id"], spawned_agents[1]["agent_id"]],
        )
        assert refresh_payload["experiment_id"] == experiment_id
        assert refresh_payload["simulation_time_count"] == 1
        assert refresh_payload["sampled_agent_count"] == 2

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            assert len(session.scalars(select(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id)).all()) >= 1
            assert len(session.scalars(select(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id)).all()) >= 1
            assert len(session.scalars(select(AgentMetric1m).where(AgentMetric1m.experiment_id == experiment.id)).all()) >= 1
            assert (
                len(
                    session.scalars(
                        select(TrajectoryPointSampled1s).where(TrajectoryPointSampled1s.experiment_id == experiment.id)
                    ).all()
                )
                >= 2
            )
        finally:
            session.close()
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)
