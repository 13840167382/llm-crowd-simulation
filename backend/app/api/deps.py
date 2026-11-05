"""FastAPI 依赖辅助函数。"""

from __future__ import annotations

from fastapi import Header

from backend.app.api import error_codes
from backend.app.api.errors import AppError


async def request_id_header(
    x_request_id: str = Header(
        ...,
        alias="X-Request-Id",
        description="客户端生成的请求唯一标识，用于日志追踪与幂等去重",
    ),
) -> str:
    """强制读取 `X-Request-Id` 请求头。"""

    return x_request_id


async def experiment_id_header(
    experiment_id: str,
    x_experiment_id: str = Header(
        ...,
        alias="X-Experiment-Id",
        description="实验 ID，实验级接口必须与路径中的 experiment_id 保持一致",
    ),
) -> str:
    """强制校验实验请求头与路径参数一致。"""

    if x_experiment_id != experiment_id:
        raise AppError(
            message="请求头 X-Experiment-Id 与路径中的 experiment_id 不一致",
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=400,
            data={
                "header_name": "X-Experiment-Id",
                "header_experiment_id": x_experiment_id,
                "path_experiment_id": experiment_id,
            },
        )
    return x_experiment_id
