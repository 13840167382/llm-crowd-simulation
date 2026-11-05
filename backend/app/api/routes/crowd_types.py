"""人群类型模板相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from backend.app.api.deps import request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.crowd_types.service import (
    clone_crowd_type as clone_crowd_type_service,
    create_crowd_type as create_crowd_type_service,
    delete_crowd_type as delete_crowd_type_service,
    get_crowd_type as get_crowd_type_service,
    list_crowd_types as list_crowd_types_service,
    update_crowd_type as update_crowd_type_service,
)
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.crowd_types import (
    CrowdTypeCloneRequest,
    CrowdTypeCreateRequest,
    CrowdTypeDeleteData,
    CrowdTypeListData,
    CrowdTypeTemplate,
    CrowdTypeUpdateRequest,
)

router = APIRouter(prefix="/crowd-types", tags=["人群类型模板"])


@router.get("", summary="获取人群类型模板列表", response_model=ApiResponse[CrowdTypeListData])
def list_crowd_types(
    include_builtin: bool = Query(default=True, description="是否包含内置默认模板"),
    keyword: str | None = Query(default=None, description="按名称模糊搜索关键词"),
    _: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    data = list_crowd_types_service(session, include_builtin=include_builtin, keyword=keyword)
    return success_response(data=data)


@router.get("/{type_id}", summary="获取单个人群类型模板", response_model=ApiResponse[CrowdTypeTemplate])
def get_crowd_type(
    type_id: str,
    _: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_crowd_type_service(session, type_id)
    return success_response(data=data)


@router.post("", summary="创建人群类型模板", response_model=ApiResponse[CrowdTypeTemplate])
def create_crowd_type(
    payload: CrowdTypeCreateRequest,
    request_id: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="crowd-types:create",
        fingerprint_data={"payload": payload.model_dump(mode="json")},
        handler=lambda: success_response(data=create_crowd_type_service(session, payload), status_code=201),
    )


@router.put("/{type_id}", summary="更新人群类型模板", response_model=ApiResponse[CrowdTypeTemplate])
def update_crowd_type(
    type_id: str,
    payload: CrowdTypeUpdateRequest,
    request_id: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="crowd-types:update",
        fingerprint_data={
            "type_id": type_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(data=update_crowd_type_service(session, type_id=type_id, payload=payload)),
    )


@router.delete("/{type_id}", summary="删除人群类型模板", response_model=ApiResponse[CrowdTypeDeleteData])
def delete_crowd_type(
    type_id: str,
    request_id: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="crowd-types:delete",
        fingerprint_data={"type_id": type_id},
        handler=lambda: success_response(data=delete_crowd_type_service(session, type_id)),
    )


@router.post("/{type_id}/clone", summary="复制人群类型模板", response_model=ApiResponse[CrowdTypeTemplate])
def clone_crowd_type(
    type_id: str,
    payload: CrowdTypeCloneRequest,
    request_id: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="crowd-types:clone",
        fingerprint_data={
            "type_id": type_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=clone_crowd_type_service(session, type_id=type_id, payload=payload),
            status_code=201,
        ),
    )
