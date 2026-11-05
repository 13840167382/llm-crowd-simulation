"""拓扑相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.topology.service import put_topology as put_topology_service
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.topology import TopologyUpsertRequest, TopologyUpsertResponseData

router = APIRouter(prefix="/experiments", tags=["拓扑"])


@router.put("/{experiment_id}/topology", summary="提交实验拓扑", response_model=ApiResponse[TopologyUpsertResponseData])
def put_topology(
    experiment_id: str,
    payload: TopologyUpsertRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    return execute_idempotent_request(
        request_id=request_id,
        operation="topology:put",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=put_topology_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )
