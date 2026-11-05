"""占位路由冒烟测试。"""

from fastapi.testclient import TestClient

from backend.app.main import create_app


def test_placeholder_routes_are_registered_in_openapi() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/openapi.json")

    assert response.status_code == 200
    paths = response.json()["paths"]

    expected_paths = {
        "/api/v1/crowd-types",
        "/api/v1/crowd-types/{type_id}",
        "/api/v1/crowd-types/{type_id}/clone",
        "/api/v1/experiments",
        "/api/v1/experiments/{experiment_id}/topology",
        "/api/v1/experiments/{experiment_id}/crowd-allocation",
        "/api/v1/experiments/{experiment_id}/spawn-positions",
        "/api/v1/experiments/{experiment_id}/start",
        "/api/v1/experiments/{experiment_id}/fail",
        "/api/v1/experiments/{experiment_id}/complete",
        "/api/v1/experiments/{experiment_id}/decisions",
        "/api/v1/experiments/{experiment_id}/trajectories",
        "/api/v1/experiments/{experiment_id}/stats",
        "/api/v1/experiments/{experiment_id}/trajectories/{agent_id}",
        "/api/v1/experiments/{experiment_id}/heatmap",
        "/api/v1/experiments/{experiment_id}/heatmap/compare",
        "/api/v1/experiments/{experiment_id}/path-traces",
    }

    assert expected_paths.issubset(paths.keys())
