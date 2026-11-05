"""实验级原始运行数据清理测试。"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select

from backend.app.application.maintenance.service import cleanup_experiment_runtime_data
from backend.app.application.trajectories.service import refresh_trajectory_views
from backend.app.infrastructure.db.models import (
    AgentDecision,
    DecisionMetric1m,
    DecisionBatch,
    ExitMetric1m,
    Experiment,
    HeatmapCache,
    TrajectoryBatch,
    TrajectoryPoint,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.unit.test_decision_flow import _cleanup_runtime_flow, _prepare_running_experiment


def test_cleanup_experiment_runtime_data_can_purge_raw_tables_and_keep_aggregates() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"cleanup_type_{suffix}"
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

        decision_response = client.post(
            f"/api/v1/experiments/{experiment_id}/decisions",
            json={
                "batch_id": f"cleanup_decision_batch_{suffix}",
                "simulation_time_ms": 1000,
                "agents": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "current_zone_id": "entry_a",
                        "stay_duration_ms": 2200,
                        "patience_ratio": 0.2,
                        "algorithm_recommendation": "exit_1",
                        "dilemma_summary": "用于清理测试的决策",
                        "available_exits": [
                            {
                                "exit_id": "exit_1",
                                "target_zone_id": "hall_1",
                                "algorithm_score": 0.88,
                                "nl_description": "唯一出口",
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
                "batch_id": f"cleanup_trajectory_batch_{suffix}",
                "simulation_time_ms": 1000,
                "sample_interval_ms": 200,
                "points": [
                    {
                        "agent_id": spawned_agents[0]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 1.2, "z": 2.3},
                        "zone_id": "entry_a",
                        "speed": 1.1,
                        "state": "walking",
                        "target_exit_id": "exit_1",
                        "is_queuing": False,
                    },
                    {
                        "agent_id": spawned_agents[1]["agent_id"],
                        "crowd_type": type_id,
                        "position": {"x": 2.2, "z": 2.9},
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
            refresh_trajectory_views(
                session,
                experiment_id=experiment_id,
                simulation_time_ms_values=[1000],
                agent_ids=[item["agent_id"] for item in spawned_agents],
            )
        finally:
            session.close()

        heatmap_response = client.post(
            f"/api/v1/experiments/{experiment_id}/heatmap",
            json={
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
                "normalization": "none",
            },
        )
        assert heatmap_response.status_code == 200

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 30000,
                "final_agent_count": 2,
            },
        )
        assert complete_response.status_code == 200

        session = get_session_factory()()
        try:
            cleanup_result = cleanup_experiment_runtime_data(session, experiment_id=experiment_id)
            assert cleanup_result.already_cleaned is False
            assert cleanup_result.deleted_counts["decision_batches"] == 1
            assert cleanup_result.deleted_counts["agent_decisions"] == 1
            assert cleanup_result.deleted_counts["trajectory_batches"] == 1
            assert cleanup_result.deleted_counts["trajectory_points"] == 2
            assert cleanup_result.deleted_counts["heatmap_cache"] == 1
        finally:
            session.close()

        session = get_session_factory()()
        try:
            experiment = session.scalar(select(Experiment).where(Experiment.experiment_id == experiment_id))
            assert experiment is not None
            assert experiment.status.value == "completed"
            assert session.scalars(select(DecisionBatch).where(DecisionBatch.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(AgentDecision).where(AgentDecision.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(TrajectoryBatch).where(TrajectoryBatch.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(TrajectoryPoint).where(TrajectoryPoint.experiment_id == experiment.id)).all() == []
            assert session.scalars(select(HeatmapCache).where(HeatmapCache.experiment_id == experiment.id)).all() == []
            assert len(session.scalars(select(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment.id)).all()) >= 1
            assert len(session.scalars(select(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment.id)).all()) >= 1
            assert len(session.scalars(select(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment.id)).all()) >= 1

            cleanup_snapshot = experiment.summary_json.get("runtime_data_cleanup")
            assert cleanup_snapshot is not None
            assert cleanup_snapshot["deleted_counts"]["trajectory_points"] == 2
            assert cleanup_snapshot["deleted_counts"]["heatmap_cache"] == 1
        finally:
            session.close()

        session = get_session_factory()()
        try:
            second_result = cleanup_experiment_runtime_data(session, experiment_id=experiment_id)
            assert second_result.already_cleaned is True
            assert second_result.deleted_counts["trajectory_points"] == 2
            assert second_result.deleted_counts["heatmap_cache"] == 1
        finally:
            session.close()
    finally:
        _cleanup_runtime_flow(experiment_id=experiment_id, type_id=type_id)
