"""决策相关路由。

路由层只处理 HTTP 依赖、请求头一致性和 X-Request-Id 幂等包装；
真正的实验状态校验、LLM/fallback 和落库逻辑都在 application.decisions.service。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.idempotency import execute_idempotent_request
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.decisions.service import create_decision_batch as create_decision_batch_service
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.decisions import DecisionBatchRequest, DecisionBatchResponseData

router = APIRouter(prefix="/experiments", tags=["决策"])


@router.post(
    "/{experiment_id}/decisions",
    summary="提交决策批次",
    response_model=ApiResponse[DecisionBatchResponseData],
)
def create_decision_batch(
    experiment_id: str,
    payload: DecisionBatchRequest,
    request_id: str = Depends(request_id_header),
    _: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    """提交一批人物决策请求。

    X-Experiment-Id 必须和路径一致；X-Request-Id 用于 Redis 幂等，避免 Unity 重试造成重复批次。
    """

    return execute_idempotent_request(
        request_id=request_id,
        operation="decisions:create",
        fingerprint_data={
            "experiment_id": experiment_id,
            "payload": payload.model_dump(mode="json"),
        },
        handler=lambda: success_response(
            data=create_decision_batch_service(
                session,
                experiment_id=experiment_id,
                payload=payload,
                request_id=request_id,
            )
        ),
    )
