"""实验相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.experiments.service import complete_experiment as complete_experiment_service
from backend.app.application.experiments.service import create_experiment as create_experiment_service
from backend.app.application.experiments.service import fail_experiment as fail_experiment_service
from backend.app.application.experiments.service import start_experiment as start_experiment_service
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.experiments import (
    ExperimentCompleteRequest,
    ExperimentCompletedData,
    ExperimentCreateRequest,
    ExperimentCreatedData,
    ExperimentFailedData,
    ExperimentFailRequest,
    ExperimentStartRequest,
    ExperimentStartedData,
)

router = APIRouter(prefix="/experiments", tags=["实验"])


@router.post("", summary="创建实验", response_model=ApiResponse[ExperimentCreatedData])
def create_experiment(
    payload: ExperimentCreateRequest,
    request_id: str = Depends(request_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="experiments:create",
        fingerprint_data={"payload": payload.model_dump(mode="json")},
        handler=lambda: success_response(data=create_experiment_service(session, payload), status_code=201),
    )


@router.post("/{experiment_id}/start", summary="启动实验", response_model=ApiResponse[ExperimentStartedData])
async def start_experiment(
    experiment_id: str,
    payload: ExperimentStartRequest | None = Body(default=None),
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="experiments:start",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json") if payload is not None else {},
        },
        handler=lambda: success_response(
            data=start_experiment_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )


@router.post("/{experiment_id}/complete", summary="完成实验", response_model=ApiResponse[ExperimentCompletedData])
async def complete_experiment(
    experiment_id: str,
    payload: ExperimentCompleteRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="experiments:complete",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=complete_experiment_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )


@router.post("/{experiment_id}/fail", summary="标记实验失败", response_model=ApiResponse[ExperimentFailedData])
async def fail_experiment(
    experiment_id: str,
    payload: ExperimentFailRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="experiments:fail",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=fail_experiment_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )
