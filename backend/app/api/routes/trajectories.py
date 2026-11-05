"""轨迹相关路由。

路由层负责 HTTP 入参和幂等包装；轨迹批次的去重、原始点落库和聚合刷新投递在
application.trajectories.service 中完成。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.trajectories.service import create_trajectory_batch as create_trajectory_batch_service
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.trajectories import TrajectoryBatchRequest, TrajectoryBatchResponseData

router = APIRouter(prefix="/experiments", tags=["轨迹"])


@router.post(
    "/{experiment_id}/trajectories",
    summary="提交轨迹批次",
    response_model=ApiResponse[TrajectoryBatchResponseData],
)
def create_trajectory_batch(
    experiment_id: str,
    payload: TrajectoryBatchRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    """提交一批轨迹点。

    Unity 可能因为网络超时重试同一批上报，所以这里同样用 X-Request-Id 做响应快照回放。
    """

    return execute_idempotent_request(
        request_id=request_id,
        operation="trajectories:create",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=create_trajectory_batch_service(session, experiment_id=experiment_id, payload=payload)
        ),
    )
