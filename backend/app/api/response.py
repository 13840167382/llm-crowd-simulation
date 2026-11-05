"""统一响应结构辅助函数。"""

from __future__ import annotations

from time import time
from typing import Any, Generic, TypeVar

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.app.core.context import get_request_id

T = TypeVar("T")


def timestamp_ms() -> int:
    return int(time() * 1000)


class ApiResponse(BaseModel, Generic[T]):
    """统一接口响应外层结构。"""

    code: int = 0
    message: str = "ok"
    data: T
    request_id: str
    timestamp_ms: int


def build_response_payload(
    *,
    code: int,
    message: str,
    data: Any | None = None,
    request_id: str | None = None,
) -> dict[str, Any]:
    payload = ApiResponse[Any](
        code=code,
        message=message,
        data={} if data is None else data,
        request_id=request_id or get_request_id(),
        timestamp_ms=timestamp_ms(),
    )
    return payload.model_dump(mode="json")


def success_response(
    *,
    data: Any | None = None,
    message: str = "ok",
    status_code: int = 200,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=build_response_payload(code=0, message=message, data=data),
    )


def error_response(
    *,
    code: int,
    message: str,
    data: Any | None = None,
    status_code: int = 400,
) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content=build_response_payload(code=code, message=message, data=data),
    )
