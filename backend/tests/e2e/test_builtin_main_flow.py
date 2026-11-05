"""基于真实 API 的主链路端到端测试。"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.application.decisions import service as decisions_service
from backend.app.domain.enums import MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.models import Experiment, ExperimentMaintenanceJob
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.infrastructure.workers.rq_worker import run_maintenance_rq_worker
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.support import cleanup_experiment_graph, prepare_running_experiment

pytestmark = pytest.mark.e2e


class SuccessfulFakeDecisionEngine:
    """用于 e2e 主链路的稳定决策引擎，避免测试访问外部模型服务。"""

    def __init__(self, *, chosen_exit_id: str) -> None:
        self.chosen_exit_id = chosen_exit_id

    def decide(self, agent, *, context, llm_timeout_ms: int, llm_model: str):
        del agent
        del context
        del llm_timeout_ms
        del llm_model
        return SimpleNamespace(
            chosen_exit_id=self.chosen_exit_id,
            reasoning="e2e fake decision result",
            confidence=0.9,
        )


def test_builtin_template_can_run_full_api_flow_to_archive(scoped_backend_settings, monkeypatch) -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    experiment_id: str | None = None

    try:
        monkeypatch.setattr(
            decisions_service,
            "get_decision_engine",
            lambda: SuccessfulFakeDecisionEngine(chosen_exit_id="exit_1"),
        )
        crowd_types_response = client.get("/api/v1/crowd-types")
        assert crowd_types_response.status_code == 200
        builtin_templates = {
            item["type_id"]: item
            for item in crowd_types_response.json()["data"]["templates"]
            if item["is_builtin"] is True
        }
        assert "tourist" in builtin_templates
        assert builtin_templates["tourist"]["display_name"] == "观光游客"

        experiment_id, spawned_agents = prepare_running_experiment(
            client,
            suffix=suffix,
            type_id="tourist",
            batch_size=4,
            fallback_to_algorithm=True,
            agent_count=2,
            create_template=False,
        )

        decision_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"e2e_decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": "tourist",
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
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": "tourist",
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2400,
                        "patience_ratio": 0.1,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "跟随推荐出口进入大厅",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "目前唯一可达出口",
                            }
                        ],
                    },
                ],
            },
        )
        assert decision_response.status_code == 200
        decision_payload = decision_response.json()["data"]
        assert decision_payload["total_count"] == 2
        assert decision_payload["success_count"] == 2
        assert len(decision_payload["decisions"]) == 2

        first_trajectory_response = client.post(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            json={
                "batch_id": f"e2e_trajectory_batch_{suffix}_1",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": "tourist",
                        "position": {"x": 1.2, "z": 2.3},
                        "zone_id": "entry_a",
                        "speed": 1.24,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": "tourist",
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
                "batch_id": f"e2e_trajectory_batch_{suffix}_2",
                "simulation_time_ms": 1200,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": "tourist",
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
        assert stats_payload["decision_stats"]["total_batches"] == 1
        assert stats_payload["agent_stats"]["total_count"] == 2

        trajectories_records_response = client.get(
            f"/api/v1/experiments/{experiment_id}/trajectories",
            params={"page": 1, "page_size": 10, "format": "records"},
        )
        assert trajectories_records_response.status_code == 200
        assert trajectories_records_response.json()["data"]["total_count"] == 3

        detail_response = client.get(
            f"/api/v1/experiments/{experiment_id}/trajectories/{spawned_agents[0]['agent_id']}",
            params={"include_decisions": "true"},
        )
        assert detail_response.status_code == 200
        detail_payload = detail_response.json()["data"]
        assert detail_payload["point_count"] == 2
        assert len(detail_payload["decisions"]) == 1

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
            "crowd_type_filter": "tourist",
            "zone_id_filter": None,
            "normalization": "min_max",
        }
        heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=heatmap_request,
        )
        assert heatmap_response.status_code == 200
        assert len(heatmap_response.json()["data"]["cells"]) == 16

        compare_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap/compare",
            json={
                "time_start_ms": heatmap_request["time_start_ms"],
                "time_end_ms": heatmap_request["time_end_ms"],
                "grid_config": heatmap_request["grid_config"],
                "metric": heatmap_request["metric"],
                "crowd_types": ["tourist"],
                "normalization": heatmap_request["normalization"],
            },
        )
        assert compare_response.status_code == 200
        assert len(compare_response.json()["data"]["layers"]) == 1

        path_traces_response = client.get(
            f"/api/v1/experiments/{experiment_id}/path-traces",
            params={"crowd_type": "tourist", "sample_rate": 1, "max_agents": 10},
        )
        assert path_traces_response.status_code == 200
        path_traces_payload = path_traces_response.json()["data"]
        assert path_traces_payload["crowd_type"] == "tourist"
        assert path_traces_payload["agent_count_in_response"] == 2

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 47000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200
        assert complete_response.json()["data"]["summary"]["total_trajectory_points"] == 3

        worker_result = run_maintenance_rq_worker(
            burst=True,
            max_jobs=10,
            with_scheduler=False,
            worker_name=f"e2e-worker-{suffix}",
        )
        assert worker_result["work_result"] is True

        cached_heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json=heatmap_request,
        )
        assert cached_heatmap_response.status_code == 200

        archived_stats_response = client.get(f"/api/v1/experiments/{experiment_id}/stats")
        assert archived_stats_response.status_code == 200
        archived_stats_payload = archived_stats_response.json()["data"]
        assert archived_stats_payload["allocation_snapshot"]["total_count"] == 2
        assert archived_stats_payload["agent_stats"]["total_count"] == 2

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None

            jobs = session.scalars(
                select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment.id)
            ).all()
            assert len(jobs) == 4
            assert all(job.status == MaintenanceJobStatus.SUCCEEDED for job in jobs)
            assert {job.job_type for job in jobs} == {
                MaintenanceJobType.ANALYTICS_REFRESH,
                MaintenanceJobType.HEATMAP_PREWARM,
                MaintenanceJobType.RUNTIME_DATA_CLEANUP,
                MaintenanceJobType.EXPERIMENT_ARCHIVE,
            }

            archive_summary = experiment.summary_json.get("archive")
            assert archive_summary is not None
            assert archive_summary["raw_runtime_retained"] is False
            assert archive_summary["cleanup_snapshot_present"] is True
        finally:
            session.close()
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id)
