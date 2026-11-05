"""人群类型模板应用服务。

本文件负责全局人群模板的业务编排，路由层只负责 HTTP 参数与响应包装。
这里集中处理几类容易出错的规则：

- 系统内置模板的初始化与保护；
- 用户自定义模板的增删改查；
- 模板被未归档实验引用时的删除约束；
- 写入实验分配表前需要固化的模板快照。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.application.common import (
    datetime_to_timestamp_ms,
    decimal_to_float,
    raise_crowd_type_conflict,
    raise_crowd_type_not_found,
    raise_crowd_type_referenced,
    raise_invalid_field_value,
)
from backend.app.application.crowd_types.builtins import (
    BUILTIN_CROWD_TYPE_SEED_METADATA,
    BUILTIN_CROWD_TYPE_TEMPLATES,
)
from backend.app.infrastructure.db.models import CrowdTypeTemplate as CrowdTypeTemplateModel
from backend.app.infrastructure.db.repositories import CrowdTypeTemplateRepository, ExperimentAllocationRepository
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.crowd_types import (
    AttractionWeights,
    CrowdTypeCloneRequest,
    CrowdTypeCreateRequest,
    CrowdTypeDeleteData,
    CrowdTypeListData,
    CrowdTypeTemplate,
    CrowdTypeUpdateRequest,
)

logger = get_logger(__name__)


def seed_builtin_crowd_types(session: Session) -> list[str]:
    """补齐系统内置模板缺失数据。

    该函数通常由 Alembic seed 迁移或初始化流程调用。它只补缺失模板，
    不覆盖已有模板，避免部署升级时重写用户已经允许修改的内置模板展示字段。
    """

    repository = CrowdTypeTemplateRepository(session)
    builtin_type_ids = [str(item["type_id"]) for item in BUILTIN_CROWD_TYPE_TEMPLATES]
    existing_type_ids = {template.type_id for template in repository.list_by_type_ids(builtin_type_ids)}
    created_type_ids: list[str] = []

    for item in BUILTIN_CROWD_TYPE_TEMPLATES:
        type_id = str(item["type_id"])
        if type_id in existing_type_ids:
            continue

        repository.add(
            CrowdTypeTemplateModel(
                type_id=type_id,
                display_name=str(item["display_name"]),
                color_hex=str(item["color_hex"]),
                is_builtin=True,
                base_speed=float(item["base_speed"]),
                social_distance=float(item["social_distance"]),
                tolerance_duration_ms=int(item["tolerance_duration_ms"]),
                attraction_weights=dict(item["attraction_weights"]),
                metadata_json=dict(BUILTIN_CROWD_TYPE_SEED_METADATA),
            )
        )
        created_type_ids.append(type_id)

    if created_type_ids:
        session.commit()
        logger.info("系统内置模板 seed 完成: created_type_ids=%s", created_type_ids)
    else:
        logger.info("系统内置模板 seed 跳过: 所有内置模板已存在")

    return created_type_ids


def list_crowd_types(
    session: Session,
    *,
    include_builtin: bool,
    keyword: str | None,
) -> CrowdTypeListData:
    """获取模板列表。"""

    repository = CrowdTypeTemplateRepository(session)
    templates = repository.list(include_builtin=include_builtin, keyword=keyword)
    total_count = repository.count(include_builtin=include_builtin, keyword=keyword)
    return CrowdTypeListData(
        total_count=total_count,
        templates=[to_crowd_type_schema(template) for template in templates],
    )


def get_crowd_type(session: Session, type_id: str) -> CrowdTypeTemplate:
    """获取单个模板。"""

    template = _get_template_or_404(session, type_id)
    return to_crowd_type_schema(template)


def create_crowd_type(session: Session, payload: CrowdTypeCreateRequest) -> CrowdTypeTemplate:
    """创建用户自定义模板。

    `type_id` 是 Unity 和后端共同使用的稳定业务 ID，因此这里在应用层先做
    显式冲突检查，数据库唯一约束作为最终兜底。
    """

    repository = CrowdTypeTemplateRepository(session)
    if repository.get_by_type_id(payload.type_id):
        raise_crowd_type_conflict(f"人群类型模板 `{payload.type_id}` 已存在")

    template = CrowdTypeTemplateModel(
        type_id=payload.type_id,
        display_name=payload.display_name,
        color_hex=payload.color_hex,
        is_builtin=False,
        base_speed=payload.base_speed,
        social_distance=payload.social_distance,
        tolerance_duration_ms=payload.tolerance_duration_ms,
        attraction_weights=payload.attraction_weights.model_dump(mode="json"),
        metadata_json=payload.metadata,
    )
    repository.add(template)
    session.commit()
    session.refresh(template)
    logger.info("模板创建完成: type_id=%s builtin=%s", template.type_id, template.is_builtin)
    return to_crowd_type_schema(template)


def update_crowd_type(
    session: Session,
    *,
    type_id: str,
    payload: CrowdTypeUpdateRequest,
) -> CrowdTypeTemplate:
    """更新模板。

    自定义模板可以更新全部可写字段；系统内置模板只允许更新展示相关字段。
    这个限制放在服务层，确保 Swagger、Unity 或后续管理端入口都走同一套规则。
    """

    template = _get_template_or_404(session, type_id)
    updates = payload.model_dump(exclude_none=True, mode="json")
    _validate_builtin_template_update(template=template, type_id=type_id, updates=updates)

    for field_name, value in updates.items():
        if field_name == "metadata":
            template.metadata_json = value
        elif field_name == "attraction_weights":
            template.attraction_weights = value
        else:
            setattr(template, field_name, value)

    session.commit()
    session.refresh(template)
    logger.info("模板更新完成: type_id=%s updated_fields=%s", template.type_id, sorted(updates))
    return to_crowd_type_schema(template)


def delete_crowd_type(session: Session, type_id: str) -> CrowdTypeDeleteData:
    """删除模板。

    删除前必须同时满足两件事：
    1. 不是系统内置模板；
    2. 没有被未归档实验引用。

    已归档实验依赖 `template_snapshot`，不再依赖在线模板表，所以不会阻止删除。
    """

    template = _get_template_or_404(session, type_id)
    if template.is_builtin:
        raise_invalid_field_value(
            f"人群类型模板 `{type_id}` 为系统内置模板，无法删除",
            data={
                "type_id": type_id,
                "reason": "builtin_template_locked",
            },
    )

    allocation_repository = ExperimentAllocationRepository(session)
    if allocation_repository.count_unarchived_experiment_references_by_type_id(type_id) > 0:
        raise_crowd_type_referenced(f"人群类型模板 `{type_id}` 仍被未归档实验引用，无法删除")

    repository = CrowdTypeTemplateRepository(session)
    repository.delete(template)
    session.commit()
    logger.info("模板删除完成: type_id=%s", type_id)
    return CrowdTypeDeleteData(type_id=type_id, deleted=True)


def clone_crowd_type(
    session: Session,
    *,
    type_id: str,
    payload: CrowdTypeCloneRequest,
) -> CrowdTypeTemplate:
    """复制模板。

    克隆用于“基于内置模板创建可编辑副本”的场景。副本会复制行为参数，
    但 `is_builtin` 固定为 `False`，保证后续可以完整编辑和删除。
    """

    source = _get_template_or_404(session, type_id)
    repository = CrowdTypeTemplateRepository(session)

    if repository.get_by_type_id(payload.new_type_id):
        raise_crowd_type_conflict(f"人群类型模板 `{payload.new_type_id}` 已存在")

    default_name = f"{source.display_name}-副本"
    display_name = (payload.new_display_name or default_name)[:32]

    # 克隆后的模板一律转为用户可编辑副本，不继承系统内置属性。
    clone = CrowdTypeTemplateModel(
        type_id=payload.new_type_id,
        display_name=display_name,
        color_hex=source.color_hex,
        is_builtin=False,
        base_speed=source.base_speed,
        social_distance=source.social_distance,
        tolerance_duration_ms=source.tolerance_duration_ms,
        attraction_weights=dict(source.attraction_weights),
        metadata_json=dict(source.metadata_json),
    )
    repository.add(clone)
    session.commit()
    session.refresh(clone)
    logger.info("模板克隆完成: source_type_id=%s new_type_id=%s", type_id, clone.type_id)
    return to_crowd_type_schema(clone)


def build_template_snapshot(template: CrowdTypeTemplateModel) -> dict[str, object]:
    """构建写入分配表的模板快照。

    实验分配保存的是模板当时的完整快照，而不是只保存 `type_id`。
    这样后面全局模板被修改或删除时，历史实验仍能复现当时的人群参数。
    """

    return {
        "type_id": template.type_id,
        "display_name": template.display_name,
        "color_hex": template.color_hex,
        "is_builtin": template.is_builtin,
        "base_speed": decimal_to_float(template.base_speed),
        "social_distance": decimal_to_float(template.social_distance),
        "tolerance_duration_ms": template.tolerance_duration_ms,
        "attraction_weights": dict(template.attraction_weights),
        "metadata": dict(template.metadata_json),
    }


def _validate_builtin_template_update(
    *,
    template: CrowdTypeTemplateModel,
    type_id: str,
    updates: dict[str, object],
) -> None:
    """限制系统内置模板只能修改 PDF 允许的字段。"""

    if not template.is_builtin:
        return

    allowed_fields = {"display_name", "color_hex", "metadata"}
    forbidden_fields = sorted(field_name for field_name in updates if field_name not in allowed_fields)
    if not forbidden_fields:
        return

    raise_invalid_field_value(
        f"人群类型模板 `{type_id}` 为系统内置模板，仅允许修改 display_name、color_hex、metadata",
        data={
            "type_id": type_id,
            "reason": "builtin_template_update_restricted",
            "allowed_fields": sorted(allowed_fields),
            "forbidden_fields": forbidden_fields,
        },
    )


def _get_template_or_404(session: Session, type_id: str) -> CrowdTypeTemplateModel:
    repository = CrowdTypeTemplateRepository(session)
    template = repository.get_by_type_id(type_id)
    if template is None:
        raise_crowd_type_not_found(f"人群类型模板 `{type_id}` 不存在")
    return template


def to_crowd_type_schema(template: CrowdTypeTemplateModel) -> CrowdTypeTemplate:
    """将 ORM 模型转换为接口 Schema。"""

    return CrowdTypeTemplate(
        type_id=template.type_id,
        display_name=template.display_name,
        color_hex=template.color_hex,
        is_builtin=template.is_builtin,
        base_speed=decimal_to_float(template.base_speed),
        social_distance=decimal_to_float(template.social_distance),
        tolerance_duration_ms=template.tolerance_duration_ms,
        attraction_weights=AttractionWeights.model_validate(template.attraction_weights),
        metadata=dict(template.metadata_json),
        created_at_ms=datetime_to_timestamp_ms(template.created_at),
        updated_at_ms=datetime_to_timestamp_ms(template.updated_at),
    )
