"""应用启动冒烟测试。"""

from fastapi.testclient import TestClient

import backend.app.main as app_main
from backend.app.main import create_app


class _DisabledRedisClient:
    """用于稳定测试 Redis 未启用场景的假客户端。"""

    def healthcheck(self) -> dict[str, object]:
        return {
            "backend": "redis",
            "enabled": False,
            "status": "disabled",
            "message": "Redis 未启用",
        }


def test_health_endpoint_returns_standard_envelope() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "ok"


def test_database_health_endpoint_returns_standard_envelope() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz/db")

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "ok"
    assert "server_version" in response.json()["data"]
    assert "timescaledb" in response.json()["data"]
    assert "available" in response.json()["data"]["timescaledb"]
    assert "installed" in response.json()["data"]["timescaledb"]
    assert "ready" in response.json()["data"]["timescaledb"]


def test_redis_health_endpoint_returns_disabled_when_redis_not_enabled(monkeypatch) -> None:
    monkeypatch.setattr(app_main, "get_redis_client", lambda: _DisabledRedisClient())
    client = TestClient(create_app())

    response = client.get("/healthz/redis")

    assert response.status_code == 200
    assert response.json()["code"] == 0
    assert response.json()["data"]["status"] == "disabled"
    assert response.json()["data"]["enabled"] is False


def test_docs_endpoint_is_available() -> None:
    client = TestClient(create_app())

    response = client.get("/api/v1/docs")

    assert response.status_code == 200


def test_analytics_dashboard_page_is_available() -> None:
    client = TestClient(create_app())

    response = client.get("/ui/analytics")

    assert response.status_code == 200
    assert "text/html" in response.headers["content-type"]
    assert '/ui/analytics.css' in response.text


def test_analytics_dashboard_css_is_available() -> None:
    client = TestClient(create_app())

    response = client.get("/ui/analytics.css")

    assert response.status_code == 200
    assert "text/css" in response.headers["content-type"]
    assert ":root" in response.text


def test_invalid_json_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/experiments",
        content='{"name": ',
        headers={
            "Content-Type": "application/json",
            "X-Request-Id": "invalid-json-test",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1001


def test_missing_required_fields_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/experiments",
        json={},
        headers={"X-Request-Id": "missing-required-fields-test"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1002


def test_invalid_field_value_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/crowd-types",
        json={
            "type_id": "Visitor",
            "display_name": "普通访客",
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
        headers={"X-Request-Id": "invalid-field-value-test"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1003
