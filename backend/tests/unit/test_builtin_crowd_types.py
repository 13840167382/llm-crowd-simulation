"""系统内置模板种子数据测试。"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy import delete, select

from backend.app.application.crowd_types.builtins import BUILTIN_CROWD_TYPE_TEMPLATES
from backend.app.application.crowd_types.service import seed_builtin_crowd_types
from backend.app.application.maintenance.service import archive_experiment
from backend.app.infrastructure.db.models import CrowdTypeTemplate
from backend.app.infrastructure.db.session import get_session_factory
from backend.app.main import create_app
from backend.tests.conftest import create_api_client
from backend.tests.support import cleanup_experiment_graph, prepare_running_experiment


def test_seed_builtin_crowd_types_can_insert_missing_templates() -> None:
    client = create_api_client(create_app())
    session = get_session_factory()()
    builtin_type_ids = [str(item["type_id"]) for item in BUILTIN_CROWD_TYPE_TEMPLATES]
    existing_type_ids: set[str] = set()
    created_type_ids: list[str] = []

    try:
        existing_type_ids = {
            template.type_id
            for template in session.scalars(
                select(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id.in_(builtin_type_ids))
            ).all()
        }
        created_type_ids = seed_builtin_crowd_types(session)
        response = client.get("/api/v1/crowd-types")
        assert response.status_code == 200

        templates = response.json()["data"]["templates"]
        builtin_templates = {item["type_id"]: item for item in templates if item["is_builtin"] is True}
        for builtin in BUILTIN_CROWD_TYPE_TEMPLATES:
            type_id = str(builtin["type_id"])
            assert type_id in builtin_templates
            assert builtin_templates[type_id]["display_name"] == builtin["display_name"]
    finally:
        cleanup_type_ids = [type_id for type_id in created_type_ids if type_id not in existing_type_ids]
        if cleanup_type_ids:
            session.execute(delete(CrowdTypeTemplate).where(CrowdTypeTemplate.type_id.in_(cleanup_type_ids)))
            session.commit()
        session.close()


def test_builtin_crowd_type_allows_pdf_whitelisted_fields_update() -> None:
    client = create_api_client(create_app())
    type_id = f"builtin_allowed_{uuid4().hex[:8]}"
    _insert_builtin_template(type_id=type_id)

    try:
        response = client.put(
            f"/api/v1/crowd-types/{type_id}",
            json={
                "display_name": "系统内置模板-已修改",
                "color_hex": "#123456",
                "metadata": {
                    "source": "unit-test",
                    "editable": True,
                },
            },
        )

        assert response.status_code == 200
        data = response.json()["data"]
        assert data["type_id"] == type_id
        assert data["is_builtin"] is True
        assert data["display_name"] == "系统内置模板-已修改"
        assert data["color_hex"] == "#123456"
        assert data["metadata"] == {"source": "unit-test", "editable": True}
        assert data["base_speed"] == 1.0
        assert data["social_distance"] == 0.8
        assert data["tolerance_duration_ms"] == 30000
    finally:
        _delete_template(type_id=type_id)


def test_cloned_builtin_crowd_type_becomes_editable_custom_template() -> None:
    client = create_api_client(create_app())
    type_id = f"builtin_clone_{uuid4().hex[:8]}"
    clone_type_id = f"builtin_copy_{uuid4().hex[:8]}"
    _insert_builtin_template(type_id=type_id)

    try:
        clone_response = client.post(
            f"/api/v1/crowd-types/{type_id}/clone",
            json={
                "new_type_id": clone_type_id,
                "new_display_name": "内置模板副本",
            },
        )

        assert clone_response.status_code == 201
        clone_data = clone_response.json()["data"]
        assert clone_data["type_id"] == clone_type_id
        assert clone_data["is_builtin"] is False
        assert clone_data["display_name"] == "内置模板副本"

        update_response = client.put(
            f"/api/v1/crowd-types/{clone_type_id}",
            json={
                "base_speed": 0.7,
                "metadata": {"source": "builtin-clone"},
            },
        )

        assert update_response.status_code == 200
        updated_data = update_response.json()["data"]
        assert updated_data["type_id"] == clone_type_id
        assert updated_data["is_builtin"] is False
        assert updated_data["base_speed"] == 0.7
        assert updated_data["metadata"] == {"source": "builtin-clone"}
    finally:
        _delete_template(type_id=clone_type_id)
        _delete_template(type_id=type_id)


def test_custom_crowd_type_delete_only_blocks_unarchived_experiment_references() -> None:
    client = create_api_client(create_app())
    suffix = uuid4().hex[:8]
    type_id = f"archive_delete_{suffix}"
    experiment_id: str | None = None

    try:
        experiment_id, spawned_agents = prepare_running_experiment(
            client,
            suffix=suffix,
            type_id=type_id,
            batch_size=1,
            fallback_to_algorithm=True,
            agent_count=1,
        )
        assert len(spawned_agents) == 1

        blocked_response = client.delete(f"/api/v1/crowd-types/{type_id}")
        assert blocked_response.status_code == 400
        assert blocked_response.json()["code"] == 5003
        assert "未归档实验引用" in blocked_response.json()["message"]

        complete_response = client.post(
            f"/api/v1/experiments/{experiment_id}/complete",
            json={
                "reason": "simulation_finished",
                "actual_duration_ms": 30000,
                "final_agent_count": 1,
            },
        )
        assert complete_response.status_code == 200

        session = get_session_factory()()
        try:
            archive_result = archive_experiment(session, experiment_id=experiment_id)
            assert archive_result.already_archived is False
        finally:
            session.close()

        deleted_response = client.delete(f"/api/v1/crowd-types/{type_id}")
        assert deleted_response.status_code == 200
        assert deleted_response.json()["data"] == {"type_id": type_id, "deleted": True}
    finally:
        cleanup_experiment_graph(experiment_id=experiment_id, type_id=type_id)


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
