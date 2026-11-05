"""维护任务查询、重置与重放回归测试。"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import delete, select

from backend.app.application.maintenance.service import (
    query_maintenance_jobs,
    replay_maintenance_jobs_from,
    reset_failed_maintenance_jobs,
)
from backend.app.domain.enums import ExperimentStatus, MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.models import Experiment, ExperimentMaintenanceJob
from backend.app.infrastructure.db.session import get_session_factory


def test_query_maintenance_jobs_can_return_status_summary() -> None:
    session = get_session_factory()()
    experiment = _create_completed_experiment(session, suffix="query")
    jobs = [
        _create_job(session, experiment_id=experiment.id, job_type=MaintenanceJobType.ANALYTICS_REFRESH, status=MaintenanceJobStatus.SUCCEEDED),
        _create_job(session, experiment_id=experiment.id, job_type=MaintenanceJobType.HEATMAP_PREWARM, status=MaintenanceJobStatus.FAILED, error_message="预热失败"),
        _create_job(session, experiment_id=experiment.id, job_type=MaintenanceJobType.RUNTIME_DATA_CLEANUP, status=MaintenanceJobStatus.PENDING),
    ]
    session.commit()

    try:
        result = query_maintenance_jobs(
            session,
            experiment_id=experiment.experiment_id,
            limit=10,
            include_payload=False,
            include_result=False,
        )
        assert result.experiment_id == experiment.experiment_id
        assert result.total_count == 3
        assert result.status_counts["succeeded"] == 1
        assert result.status_counts["failed"] == 1
        assert result.status_counts["pending"] == 1
        assert result.job_type_counts["analytics_refresh"] == 1
        assert result.job_type_counts["heatmap_prewarm"] == 1
        assert result.job_type_counts["runtime_data_cleanup"] == 1
        assert [job.job_id for job in result.jobs] == [int(job.id) for job in jobs]
        assert result.jobs[1].error_message == "预热失败"
        assert result.jobs[0].payload_json is None
        assert result.jobs[0].result_json is None
    finally:
        _cleanup_experiment(session, experiment.id)


def test_reset_failed_maintenance_jobs_can_reset_failed_rows_and_dispatch(monkeypatch) -> None:
    session = get_session_factory()()
    experiment = _create_completed_experiment(
        session,
        suffix="reset",
        summary_json={
            "runtime_data_cleanup": {"cleaned_at_ms": 1700000000000},
            "archive": {"archived_at_ms": 1700000005000},
        },
    )
    failed_job = _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.RUNTIME_DATA_CLEANUP,
        status=MaintenanceJobStatus.FAILED,
        attempt_count=2,
        error_message="清理失败",
        result_json={"deleted_counts": {"trajectory_points": 12}},
    )
    _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.EXPERIMENT_ARCHIVE,
        status=MaintenanceJobStatus.PENDING,
    )
    session.commit()

    monkeypatch.setattr(
        "backend.app.infrastructure.workers.rq_worker.dispatch_pending_maintenance_jobs",
        lambda limit=20, fanout=1: _FakeDispatchResult(status="enqueued", queue_name="maintenance"),
    )

    try:
        result = reset_failed_maintenance_jobs(
            session,
            experiment_id=experiment.experiment_id,
            dispatch=True,
        )
        assert result.action == "reset_failed"
        assert result.reset_count == 1
        assert result.affected_job_ids == [int(failed_job.id)]
        assert result.affected_job_types == ["runtime_data_cleanup"]
        assert result.cleared_summary_keys == ["runtime_data_cleanup"]
        assert result.dispatch_result is not None
        assert result.dispatch_result["status"] == "enqueued"

        refreshed_failed_job = session.get(ExperimentMaintenanceJob, failed_job.id)
        assert refreshed_failed_job is not None
        assert refreshed_failed_job.status == MaintenanceJobStatus.PENDING
        assert refreshed_failed_job.attempt_count == 0
        assert refreshed_failed_job.error_message is None
        assert refreshed_failed_job.result_json == {}
        refreshed_experiment = session.get(Experiment, experiment.id)
        assert refreshed_experiment is not None
        assert "runtime_data_cleanup" not in dict(refreshed_experiment.summary_json or {})
        assert "archive" in dict(refreshed_experiment.summary_json or {})
    finally:
        _cleanup_experiment(session, experiment.id)


def test_replay_maintenance_jobs_from_can_reset_chain_and_clear_summary(monkeypatch) -> None:
    session = get_session_factory()()
    experiment = _create_completed_experiment(
        session,
        suffix="replay",
        summary_json={
            "runtime_data_cleanup": {"cleaned_at_ms": 1700000100000},
            "archive": {"archived_at_ms": 1700000200000},
        },
    )
    analytics_job = _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.ANALYTICS_REFRESH,
        status=MaintenanceJobStatus.SUCCEEDED,
        result_json={"trajectory_time_count": 1},
    )
    prewarm_job = _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.HEATMAP_PREWARM,
        status=MaintenanceJobStatus.SUCCEEDED,
        result_json={"prewarmed_count": 2},
    )
    cleanup_job = _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.RUNTIME_DATA_CLEANUP,
        status=MaintenanceJobStatus.SUCCEEDED,
        result_json={"deleted_counts": {"trajectory_points": 8}},
    )
    archive_job = _create_job(
        session,
        experiment_id=experiment.id,
        job_type=MaintenanceJobType.EXPERIMENT_ARCHIVE,
        status=MaintenanceJobStatus.SUCCEEDED,
        result_json={"raw_runtime_retained": False},
    )
    session.commit()

    monkeypatch.setattr(
        "backend.app.infrastructure.workers.rq_worker.dispatch_pending_maintenance_jobs",
        lambda limit=20, fanout=1: _FakeDispatchResult(status="enqueued", queue_name="maintenance"),
    )

    try:
        result = replay_maintenance_jobs_from(
            session,
            experiment_id=experiment.experiment_id,
            from_job_id=int(cleanup_job.id),
            dispatch=True,
        )
        assert result.action == "replay_from_job"
        assert result.reset_count == 2
        assert result.affected_job_ids == [int(cleanup_job.id), int(archive_job.id)]
        assert result.affected_job_types == ["runtime_data_cleanup", "experiment_archive"]
        assert result.cleared_summary_keys == ["runtime_data_cleanup", "archive"]
        assert result.dispatch_result is not None
        assert result.dispatch_result["queue_name"] == "maintenance"

        refreshed_analytics = session.get(ExperimentMaintenanceJob, analytics_job.id)
        refreshed_prewarm = session.get(ExperimentMaintenanceJob, prewarm_job.id)
        refreshed_cleanup = session.get(ExperimentMaintenanceJob, cleanup_job.id)
        refreshed_archive = session.get(ExperimentMaintenanceJob, archive_job.id)
        assert refreshed_analytics is not None and refreshed_analytics.status == MaintenanceJobStatus.SUCCEEDED
        assert refreshed_prewarm is not None and refreshed_prewarm.status == MaintenanceJobStatus.SUCCEEDED
        assert refreshed_cleanup is not None and refreshed_cleanup.status == MaintenanceJobStatus.PENDING
        assert refreshed_cleanup.attempt_count == 0
        assert refreshed_cleanup.result_json == {}
        assert refreshed_archive is not None and refreshed_archive.status == MaintenanceJobStatus.PENDING
        assert refreshed_archive.attempt_count == 0
        assert refreshed_archive.result_json == {}

        refreshed_experiment = session.get(Experiment, experiment.id)
        assert refreshed_experiment is not None
        assert refreshed_experiment.summary_json == {}
    finally:
        _cleanup_experiment(session, experiment.id)


class _FakeDispatchResult:
    """伪造的 RQ 派发结果。"""

    def __init__(self, *, status: str, queue_name: str) -> None:
        self.status = status
        self.queue_name = queue_name

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "queue_name": self.queue_name,
            "enqueued_count": 1,
            "due_job_count": 1,
            "next_scheduled_delay_seconds": None,
            "rq_job_ids": ["fake-rq-job"],
            "message": "测试派发结果",
        }


def _create_completed_experiment(
    session,
    *,
    suffix: str,
    summary_json: dict[str, object] | None = None,
) -> Experiment:
    experiment = Experiment(
        experiment_id=f"exp_{suffix}_{uuid4().hex[:8]}",
        name=f"维护任务测试-{suffix}",
        description="用于维护任务管理测试",
        map_id="maintenance-map",
        status=ExperimentStatus.COMPLETED,
        llm_model="gpt-4.1-mini",
        llm_temperature=0.6,
        llm_max_tokens=256,
        llm_timeout_ms=5000,
        decision_batch_interval_ms=1000,
        decision_batch_size=10,
        fallback_to_algorithm=True,
        tags=[],
        summary_json=summary_json or {},
    )
    session.add(experiment)
    session.flush()
    return experiment


def _create_job(
    session,
    *,
    experiment_id: int,
    job_type: MaintenanceJobType,
    status: MaintenanceJobStatus,
    attempt_count: int = 0,
    error_message: str | None = None,
    payload_json: dict[str, object] | None = None,
    result_json: dict[str, object] | None = None,
) -> ExperimentMaintenanceJob:
    job = ExperimentMaintenanceJob(
        experiment_id=experiment_id,
        job_type=job_type,
        status=status,
        attempt_count=attempt_count,
        payload_json=payload_json or {},
        result_json=result_json or {},
        error_message=error_message,
        scheduled_at=datetime.now(timezone.utc),
    )
    session.add(job)
    session.flush()
    return job


def _cleanup_experiment(session, experiment_pk: int) -> None:
    session.execute(delete(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment_pk))
    session.execute(delete(Experiment).where(Experiment.id == experiment_pk))
    session.commit()
    session.close()
