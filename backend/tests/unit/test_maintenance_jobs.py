"""实验维护任务后台执行回归测试。"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, select

from backend.app.api.errors import AppError
from backend.app.application.maintenance import service as maintenance_service
from backend.app.application.maintenance.service import run_pending_maintenance_jobs
from backend.app.core.context import get_experiment_id, get_request_id
from backend.app.domain.enums import MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.models import (
    DecisionBatch,
    Experiment,
    ExperimentArchive,
    ExperimentMaintenanceJob,
    HeatmapCache,
    TrajectoryBatch,
    TrajectoryPoint,
)
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.workers import rq_worker as rq_worker_adapter
from backend.app.infrastructure.workers import tasks as worker_tasks
from backend.app.infrastructure.workers.tasks import run_maintenance_worker
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.unit.test_decision_flow import _cleanup_runtime_flow, _prepare_running_experiment


def test_complete_can_enqueue_cleanup_job_and_worker_can_process_it(monkeypatch) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(ExperimentMaintenanceJob))
        session.commit()
    finally:
        session.close()

    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"maintenance_type_{suffix}"
    experiment_id: str | None = None
    queued_job_ids: list[int] = []

    try:
        monkeypatch.setattr(
            rq_worker_adapter,
            "dispatch_pending_maintenance_jobs",
            lambda *args, **kwargs: SimpleNamespace(status="disabled", rq_job_ids=[]),
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
                "batch_id": f"maintenance_decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2600,
                        "patience_ratio": 0.16,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于测试完成后自动入队清理任务",
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
                "batch_id": f"maintenance_trajectory_batch_{suffix}",
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

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            jobs = session.scalars(
                select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
            ).all()
            assert len(jobs) == 3
            assert {job.job_type for job in jobs} == {
                MaintenanceJobType.ANALYTICS_REFRESH,
                MaintenanceJobType.HEATMAP_PREWARM,
                MaintenanceJobType.RUNTIME_DATA_CLEANUP,
            }
            assert all(job.status == MaintenanceJobStatus.PENDING for job in jobs)
            queued_job_ids = [job.id for job in jobs]
        finally:
            session.close()

        _force_pending_jobs_due(experiment_id)

        session = get_session_factory()()
        try:
            execution_result = run_pending_maintenance_jobs(session, limit=100)
            assert execution_result.processed_count == 4
            assert set(queued_job_ids).issubset(set(execution_result.job_ids))
            assert execution_result.succeeded_count == 4
            assert execution_result.failed_count == 0
            assert execution_result.skipped_count == 0
        finally:
            session.close()

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            jobs = session.scalars(
                select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
            ).all()
            assert len(jobs) == 4
            status_by_type = {job.job_type: job for job in jobs}
            assert status_by_type[MaintenanceJobType.ANALYTICS_REFRESH].status == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.HEATMAP_PREWARM].status == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.RUNTIME_DATA_CLEANUP].status == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.EXPERIMENT_ARCHIVE].status == MaintenanceJobStatus.SUCCEEDED
            assert status_by_type[MaintenanceJobType.ANALYTICS_REFRESH].result_json["trajectory_time_count"] == 1
            assert status_by_type[MaintenanceJobType.HEATMAP_PREWARM].result_json["prewarmed_count"] == 2
            assert status_by_type[MaintenanceJobType.HEATMAP_PREWARM].result_json["grid_config"]["cols"] == 4
            assert status_by_type[MaintenanceJobType.HEATMAP_PREWARM].result_json["grid_config"]["rows"] == 2
            assert status_by_type[MaintenanceJobType.RUNTIME_DATA_CLEANUP].result_json["deleted_counts"]["trajectory_points"] == 2
            assert status_by_type[MaintenanceJobType.RUNTIME_DATA_CLEANUP].result_json["deleted_counts"]["heatmap_cache"] == 0
            assert status_by_type[MaintenanceJobType.EXPERIMENT_ARCHIVE].result_json["raw_runtime_retained"] is False
            assert status_by_type[MaintenanceJobType.EXPERIMENT_ARCHIVE].result_json["heatmap_cache_count"] == 2

            assert session.scalars(select(DecisionBatch).where(DecisionBatch.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(TrajectoryBatch).where(TrajectoryBatch.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(TrajectoryPoint).where(TrajectoryPoint.experiment_id == experiment.id)).all() == []
            cache_rows = session.scalars(select(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id)).all()
            assert len(cache_rows) == 2

            cleanup_snapshot = experiment.summary_json.get("runtime_data_cleanup")
            assert cleanup_snapshot is not None
            assert cleanup_snapshot["deleted_counts"]["trajectory_points"] == 2
            assert cleanup_snapshot["deleted_counts"]["heatmap_cache"] == 0
            archive_snapshot = experiment.summary_json.get("archive")
            assert archive_snapshot is not None
            assert archive_snapshot["raw_runtime_retained"] is False
            assert archive_snapshot["cleanup_snapshot_present"] is True
            assert archive_snapshot["aggregate_snapshot"]["decision_metric_buckets"] >= 1
            assert archive_snapshot["maintenance_job_statuses"]["runtime_data_cleanup"] == "succeeded"
            assert archive_snapshot["maintenance_job_statuses"]["experiment_archive"] == "succeeded"
            assert archive_snapshot["archive_id"] is not None
            archive_row = session.scalar(
                select(ExperimentArchive).where(ExperimentArchive.experiment_id == experiment_id)
            )
            assert archive_row is not None
            assert archive_row.experiment_name == experiment.name
            assert archive_row.archive_version == "v1"
            assert archive_row.summary_json["archive_id"] == archive_snapshot["archive_id"]
            assert archive_row.archive_payload_json["experiment_snapshot"]["experiment_id"] == experiment_id
            assert archive_row.archive_payload_json["allocation_snapshot"]["crowd_type_snapshots"][type_id]["type_id"] == type_id
        finally:
            session.close()

        cached_heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json={
                "time_start_ms": 1000,
                "time_end_ms": 1000,
                "grid_config": {
                    "origin": {"x": 0.0, "z": 0.0},
                    "cell_size": 10.0,
                    "cols": 4,
                    "rows": 2,
                },
                "metric": "passage_count",
                "crowd_type_filter": type_id,
                "zone_id_filter": None,
                "normalization": "min_max",
            },
        )
        assert cached_heatmap_response.status_code == 200
        cached_heatmap_payload = cached_heatmap_response.json()["data"]
        assert cached_heatmap_payload["statistics"]["non_empty_cells"] >= 1
        assert len(cached_heatmap_payload["cells"]) == 8

        missing_cache_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json={
                "time_start_ms": 1000,
                "time_end_ms": 1000,
                "grid_config": {
                    "origin": {"x": 0.0, "z": 0.0},
                    "cell_size": 10.0,
                    "cols": 4,
                    "rows": 2,
                },
                "metric": "queue_frequency",
                "crowd_type_filter": type_id,
                "zone_id_filter": None,
                "normalization": "min_max",
            },
        )
        assert missing_cache_response.status_code == 409
        assert missing_cache_response.json()["code"] == 2002

        session = get_session_factory()()
        try:
            second_result = run_pending_maintenance_jobs(session, limit=100)
            assert second_result.processed_count == 0
            assert second_result.succeeded_count == 0
            assert second_result.failed_count == 0
            assert second_result.skipped_count == 0
        finally:
            session.close()
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_maintenance_job_can_retry_with_backoff_without_releasing_following_jobs(monkeypatch) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(ExperimentMaintenanceJob))
        session.commit()
    finally:
        session.close()

    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"maintenance_retry_{suffix}"
    experiment_id: str | None = None
    original_refresh = maintenance_service.refresh_experiment_analytics
    call_state = {"count": 0}

    def flaky_refresh(session, *, experiment_id: str):
        call_state["count"] += 1
        if call_state["count"] == 1:
            raise RuntimeError("模拟 analytics_refresh 首次失败")
        return original_refresh(session, experiment_id=experiment_id)

    try:
        monkeypatch.setattr(maintenance_service, "refresh_experiment_analytics", flaky_refresh)
        monkeypatch.setattr(
            rq_worker_adapter,
            "dispatch_pending_maintenance_jobs",
            lambda *args, **kwargs: SimpleNamespace(status="disabled", rq_job_ids=[]),
        )
        monkeypatch.setattr(
            maintenance_service,
            "get_settings",
            lambda: SimpleNamespace(
                experiment_runtime_cleanup_auto_enqueue=True,
                heatmap_prewarm_enabled=True,
                maintenance_job_max_retry_attempts=3,
                maintenance_job_retry_backoff_seconds=60.0,
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

        decision_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"maintenance_retry_decision_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2600,
                        "patience_ratio": 0.16,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证维护任务失败后按顺序重试",
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
                "batch_id": f"maintenance_retry_trajectory_{suffix}",
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
                    }
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

        session = get_session_factory()()
        try:
            first_result = run_pending_maintenance_jobs(session, limit=100)
            assert first_result.processed_count == 1
            assert first_result.succeeded_count == 0
            assert first_result.failed_count == 0
            assert first_result.skipped_count == 0
            assert first_result.retried_count == 1
        finally:
            session.close()

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            jobs = session.scalars(
                select(ExperimentMaintenanceJob)
                .where(ExperimentMaintenanceJob.experiment_id == experiment.id)
                .order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
            ).all()
            assert len(jobs) == 3
            assert jobs[0].job_type == MaintenanceJobType.ANALYTICS_REFRESH
            assert jobs[0].status == MaintenanceJobStatus.PENDING
            assert jobs[0].attempt_count == 1
            assert jobs[0].scheduled_at is not None
            assert jobs[0].scheduled_at > datetime.now(timezone.utc) - timedelta(seconds=1)
            assert jobs[1].status == MaintenanceJobStatus.PENDING
            assert jobs[1].attempt_count == 0
            assert jobs[2].status == MaintenanceJobStatus.PENDING
            assert jobs[2].attempt_count == 0

            due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            for job in jobs:
                job.scheduled_at = due_at
            session.commit()
        finally:
            session.close()

        session = get_session_factory()()
        try:
            second_result = run_pending_maintenance_jobs(session, limit=100)
            assert second_result.processed_count == 4
            assert second_result.succeeded_count == 4
            assert second_result.failed_count == 0
            assert second_result.skipped_count == 0
            assert second_result.retried_count == 0
        finally:
            session.close()
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def test_async_worker_fails_fast_when_startup_db_precheck_fails(monkeypatch) -> None:
    monkeypatch.setattr(
        worker_tasks,
        "check_database_health",
        lambda: {
            "backend": "postgresql",
            "connected": False,
            "status": "error",
            "database": "crowd_flow_simulation_local",
            "message": "数据库连接失败: mock",
        },
    )

    with pytest.raises(AppError) as exc_info:
        asyncio.run(
            run_maintenance_worker(
                limit=20,
                poll_interval_seconds=0.01,
                stop_when_idle=True,
                max_cycles=1,
            )
        )

    assert exc_info.value.code == 4002


def test_async_maintenance_worker_can_poll_until_idle(monkeypatch) -> None:
    session = get_session_factory()()
    try:
        session.execute(delete(ExperimentMaintenanceJob))
        session.commit()
    finally:
        session.close()

    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"maintenance_async_{suffix}"
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            rq_worker_adapter,
            "dispatch_pending_maintenance_jobs",
            lambda *args, **kwargs: SimpleNamespace(status="disabled", rq_job_ids=[]),
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
                "batch_id": f"maintenance_async_decision_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2600,
                        "patience_ratio": 0.16,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于验证常驻 worker 异步轮询执行",
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
                "batch_id": f"maintenance_async_trajectory_{suffix}",
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
                    }
                ],
            },
        )
        assert trajectory_response.status_code == 200

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 28000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200

        _force_pending_jobs_due(experiment_id)

        worker_result = asyncio.run(
            run_maintenance_worker(
                limit=100,
                poll_interval_seconds=0.01,
                stop_when_idle=True,
                max_cycles=5,
            )
        )
        assert worker_result.stop_reason == "idle"
        assert worker_result.poll_count >= 2
        assert worker_result.active_poll_count >= 1
        assert worker_result.idle_poll_count >= 1
        assert worker_result.processed_count == 4
        assert worker_result.succeeded_count == 4
        assert worker_result.failed_count == 0
        assert worker_result.skipped_count == 0
        assert worker_result.uptime_ms >= 0
        assert worker_result.worker_id

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            jobs = session.scalars(
                select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
            ).all()
            assert len(jobs) == 4
            assert all(job.status == MaintenanceJobStatus.SUCCEEDED for job in jobs)
        finally:
            session.close()
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)


def _force_pending_jobs_due(experiment_id: str) -> None:
    session = get_session_factory()()
    try:
        experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
        assert experiment is not None
        jobs = session.scalars(
            select(ExperimentMaintenanceJob)
            .where(
                ExperimentMaintenanceJob.experiment_id == experiment.id,
                ExperimentMaintenanceJob.status == MaintenanceJobStatus.PENDING,
            )
            .order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
        ).all()
        due_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        for job in jobs:
            job.scheduled_at = due_at
        session.commit()
    finally:
        session.close()


def test_run_pending_maintenance_jobs_injects_experiment_context(monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"maintenance_ctx_{suffix}"
    experiment_id: str | None = None
    observed: dict[str, str | None] = {}

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

        def fake_refresh(session, *, experiment_id: str):
            del session
            observed["request_id"] = get_request_id()
            observed["experiment_id"] = get_experiment_id()
            return maintenance_service.ExperimentAnalyticsRefreshResult(
                experiment_id=experiment_id,
                status="completed",
                decision_time_count=0,
                trajectory_time_count=0,
                refreshed_decision_buckets=0,
                refreshed_trajectory_buckets=0,
            )

        monkeypatch.setattr(maintenance_service, "refresh_experiment_analytics", fake_refresh)

        session = get_session_factory()()
        try:
            result = run_pending_maintenance_jobs(session, limit=1)
            assert result.processed_count == 1
            assert result.succeeded_count == 1
        finally:
            session.close()

        assert observed["experiment_id"] == experiment_id
        assert observed["request_id"] is not None
        assert observed["request_id"].startswith("maintenance-job-")
        assert get_request_id() == "-"
        assert get_experiment_id() is None
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)
