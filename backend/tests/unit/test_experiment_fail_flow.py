"""实验失败状态流转回归测试。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import select

from backend.app.domain.enums import CompletionReason, ExperimentStatus
from backend.app.infrastructure.db.models import Experiment
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.support import cleanup_experiment_graph, prepare_running_experiment


def test_running_experiment_can_transition_to_failed() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"failed_type_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, _ = prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=2,
        )

        response = client.post(
            f"/api/v1/experiments/{experiment_id}/fail",
            json={
                "reason": "unity_runtime_error",
                "message": "navigation solver crashed",
                "actual_duration_ms": 1234,
                "final_agent_count": 2,
                "details": {"frame": 456},
            },
        )

        assert response.status_code == 200
        payload = response.json()["data"]
        assert payload["experiment_id"] == experiment_id
        assert payload["status"] == "failed"
        assert payload["failed_at_ms"] == 1700000201234
        assert payload["summary"] == {
            "reason": "unity_runtime_error",
            "message": "navigation solver crashed",
            "failed_at_ms": 1700000201234,
            "duration_ms": 1234,
            "final_agent_count": 2,
            "previous_status": "running",
            "details": {"frame": 456},
        }

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            assert experiment.status == ExperimentStatus.FAILED
            assert experiment.completion_reason == CompletionReason.ERROR
            assert experiment.simulation_completed_at_ms == 1700000201234
            assert experiment.actual_duration_ms == 1234
            assert experiment.final_agent_count == 2
            assert experiment.summary_json["failure"] == payload["summary"]
        finally:
            session.close()
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id, type_id=type_id)


def test_failed_experiment_rejects_later_terminal_transition() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"failed_terminal_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, _ = prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=2,
            fallback_to_algorithm=True,
            agent_count=1,
        )
        fail_response = client.post(
            f"/api/v1/experiments/{experiment_id}/fail",
            json={"reason": "manual_abort", "failed_at_ms": 1700000205000},
        )
        assert fail_response.status_code == 200

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 6000,
                "final_agent_count": 1,
            },
        )
        assert complete_response.status_code == 409
        assert complete_response.json()["code"] == 2002

        fail_again_response = client.post(
            f"/api/v1/experiments/{experiment_id}/fail",
            json={"reason": "duplicate_failure"},
        )
        assert fail_again_response.status_code == 409
        assert fail_again_response.json()["code"] == 2002
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id, type_id=type_id)
