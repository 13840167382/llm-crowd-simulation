"""人群分配与出生点相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.allocation_spawn.service import (
    create_spawn_positions as create_spawn_positions_service,
    get_crowd_allocation as get_crowd_allocation_service,
    put_crowd_allocation as put_crowd_allocation_service,
)
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.allocation import (
    CrowdAllocationRequest,
    CrowdAllocationResponseData,
    SpawnPositionsRequest,
    SpawnPositionsResponseData,
)

router = APIRouter(prefix="/experiments", tags=["人群分配与出生点"])


@router.put(
    "/{experiment_id}/crowd-allocation",
    summary="配置人群分配",
    response_model=ApiResponse[CrowdAllocationResponseData],
)
def put_crowd_allocation(
    experiment_id: str,
    payload: CrowdAllocationRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="allocation:put",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=put_crowd_allocation_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )


@router.get(
    "/{experiment_id}/crowd-allocation",
    summary="获取人群分配配置",
    response_model=ApiResponse[CrowdAllocationResponseData],
)
def get_crowd_allocation(
    experiment_id: str,
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_crowd_allocation_service(session, experiment_id=experiment_id)
    return success_response(data=data)


@router.post(
    "/{experiment_id}/spawn-positions",
    summary="生成出生点",
    response_model=ApiResponse[SpawnPositionsResponseData],
)
async def create_spawn_positions(
    experiment_id: str,
    payload: SpawnPositionsRequest | None = Body(default=None),
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="spawn-positions:create",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json") if payload is not None else {},
        },
        handler=lambda: success_response(
            data=create_spawn_positions_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )
