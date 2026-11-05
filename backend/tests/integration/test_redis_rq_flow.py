"""真实 PostgreSQL / Redis / RQ 集成测试。"""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.domain.enums import MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import Experiment, ExperimentMaintenanceJob
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.workers.rq_worker import _import_rq_components, run_maintenance_rq_worker
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.support import cleanup_experiment_graph, prepare_running_experiment

pytestmark = pytest.mark.integration


def test_create_experiment_can_replay_response_from_real_redis(scoped_backend_settings) -> None:
    client = create_api_client(create_app())
    request_id = f"integration-idempotency-{uuid4().hex[:8]}"
    experiment_id: str | None = None

    try:
        payload = {
            "name": f"Redis 幂等测试-{uuid4().hex[:8]}",
            "description": "验证真实 Redis 下的 X-Request-Id 回放",
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
            "tags": ["integration", "redis"],
        }

        first_response = client.post(
            "/api/v1/experiments",
            json=payload,
            headers={"X-Request-Id": request_id},
        )
        assert first_response.status_code == 201
        first_payload = first_response.json()["data"]
        experiment_id = first_payload["experiment_id"]

        second_response = client.post(
            "/api/v1/experiments",
            json=payload,
            headers={"X-Request-Id": request_id},
        )
        assert second_response.status_code == 201
        assert second_response.json()["data"] == first_payload

        cached_snapshot = get_redis_client().get_json(
            "idempotency",
            "response",
            "experiments:create",
            request_id,
        )
        assert isinstance(cached_snapshot, dict)
        assert cached_snapshot["status_code"] == 201
        assert cached_snapshot["payload"]["data"]["experiment_id"] == experiment_id
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id)


def test_complete_flow_can_dispatch_and_consume_maintenance_jobs_via_real_rq(
    scoped_backend_settings,
) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"rq_integration_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = prepare_running_experiment(
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
                "batch_id": f"rq_decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2600,
                        "patience_ratio": 0.16,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证真实 RQ 维护链路",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.86,
                                "nl_description": "通往大厅 1",
                            }
                        ],
                    }
                ],
            },
        )
        assert decision_response.status_code == 200

        trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"rq_trajectory_batch_{suffix}",
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

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 32000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200

        queue_class, _ = _import_rq_components()
        queue = queue_class(
            scoped_backend_settings.maintenance_queue_name,
            connection=get_redis_client().get_rq_connection(),
        )
        assert queue.count > 0

        worker_result = run_maintenance_rq_worker(
            burst=True,
            max_jobs=10,
            with_scheduler=False,
            worker_name=f"integration-rq-{suffix}",
        )
        assert worker_result["queue_name"] == scoped_backend_settings.maintenance_queue_name
        assert worker_result["work_result"] is True
        assert queue.count == 0

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None

            jobs = session.scalars(
                select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
            ).all()
            assert len(jobs) == 4

            status_by_type = {job.job_type: job.status for job in jobs}
            assert status_by_type[MaintenanceJobType.ANALYTICS_REFRESH] == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.HEATMAP_PREWARM] == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.RUNTIME_DATA_CLEANUP] == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.EXPERIMENT_ARCHIVE] == MaintenanceJobStatus.SUCCEEDED

            archive_summary = experiment.summary_json.get("archive")
            assert archive_summary is not None
            assert archive_summary["raw_runtime_retained"] is False
            assert archive_summary["cleanup_snapshot_present"] is True
            assert archive_summary["maintenance_job_statuses"]["experiment_archive"] == "succeeded"
        finally:
            session.close()
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id, type_id=type_id)
