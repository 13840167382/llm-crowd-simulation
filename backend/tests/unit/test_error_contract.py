"""错误码契约测试。"""

from __future__ import annotations

from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from backend.app.infrastructure.db.models import CrowdTypeTemplate
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.main import create_app


def test_missing_experiment_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    experiment_id = f"exp_missing_{uuid4().hex[:8]}"
    response = client.get(
        f"/api/v1/experiments/{experiment_id}/crowd-allocation",
        headers={
            "X-Request-Id": f"missing-experiment-{uuid4().hex[:8]}",
            "X-Experiment-Id": experiment_id,
        },
    )

    assert response.status_code == 404
    assert response.json()["code"] == 2001


def test_missing_crowd_type_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    type_id = f"missing_{uuid4().hex[:8]}"
    response = client.get(
        f"/api/v1/crowd-types/{type_id}",
        headers={"X-Request-Id": f"missing-crowd-type-{uuid4().hex[:8]}"},
    )

    assert response.status_code == 404
    assert response.json()["code"] == 5001


def test_missing_request_id_header_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/api/v1/experiments",
        json={
            "name": "缺少请求头测试",
            "description": "验证 X-Request-Id 缺失时返回 1002",
            "map_id": "demo-map",
            "llm_config": {
                "model": "gpt-4.1-mini",
                "temperature": 0.6,
                "max_tokens": 256,
                "timeout_ms": 5000,
            },
            "decision_config": {
                "batch_interval_ms": 1000,
                "batch_size": 20,
                "fallback_to_algorithm": True,
            },
            "tags": ["header-test"],
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1002


def test_missing_experiment_header_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    experiment_id = f"exp_missing_header_{uuid4().hex[:8]}"
    response = client.get(
        f"/api/v1/experiments/{experiment_id}/crowd-allocation",
        headers={"X-Request-Id": f"missing-experiment-header-{uuid4().hex[:8]}"},
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1002


def test_mismatched_experiment_header_returns_pdf_error_code() -> None:
    client = TestClient(create_app())

    experiment_id = f"exp_path_{uuid4().hex[:8]}"
    response = client.get(
        f"/api/v1/experiments/{experiment_id}/crowd-allocation",
        headers={
            "X-Request-Id": f"mismatch-experiment-header-{uuid4().hex[:8]}",
            "X-Experiment-Id": f"exp_header_{uuid4().hex[:8]}",
        },
    )

    assert response.status_code == 400
    assert response.json()["code"] == 1003


def test_builtin_crowd_type_delete_returns_pdf_error_code() -> None:
    client = TestClient(create_app())
    type_id = f"builtin_delete_{uuid4().hex[:8]}"
    _insert_builtin_template(type_id=type_id)

    try:
        response = client.delete(
            f"/api/v1/crowd-types/{type_id}",
            headers={"X-Request-Id": f"builtin-delete-{uuid4().hex[:8]}"},
        )

        assert response.status_code == 400
        assert response.json()["code"] == 1003
    finally:
        _delete_template(type_id=type_id)


def test_builtin_crowd_type_update_returns_pdf_error_code() -> None:
    client = TestClient(create_app())
    type_id = f"builtin_update_{uuid4().hex[:8]}"
    _insert_builtin_template(type_id=type_id)

    try:
        response = client.put(
            f"/api/v1/crowd-types/{type_id}",
            headers={"X-Request-Id": f"builtin-update-{uuid4().hex[:8]}"},
            json={"base_speed": 0.7},
        )

        assert response.status_code == 400
        assert response.json()["code"] == 1003
    finally:
        _delete_template(type_id=type_id)


def _insert_builtin_template(*, type_id: str) -> None:
    session = get_session_factory()()
    try:
        template = CrowdTypeTemplate(
            type_id=type_id,
            display_name="系统内置模板",
            color_hex="#336699",
            is_builtin=True,
            base_speed=1.0,
            social_distance=0.8,
            tolerance_duration_ms=30000,
            attraction_weights={
                "exhibit": 0.4,
                "commercial": 0.4,
                "rest": 0.2,
                "service": 0.2,
                "plaza": 0.4,
                "passage": 0.5,
                "exit_final": 0.1,
            },
            metadata_json={},
        )
        session.add(template)
        session.commit()
    finally:
        session.close()


def _delete_template(*, type_id: str) -> None:
    session = get_session_factory()()
    try:
        template = session.scalar(select(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id == type_id))
        if template is not None:
            session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.id == template.id))
            session.commit()
    finally:
        session.close()
