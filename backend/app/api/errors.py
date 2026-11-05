"""异常注册与接口错误类型定义。"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError

from backend.app.api import error_codes
from backend.app.api.response import error_response
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


class AppError(Exception):
    """带有接口元信息的应用层异常。"""

    def __init__(
        self,
        message: str,
        *,
        code: int,
        status_code: int = 400,
        data: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.data = data or {}


async def handle_app_error(_: Request, exc: AppError):
    logger.warning(
        "应用异常: code=%s status=%s message=%s data=%s",
        exc.code,
        exc.status_code,
        exc.message,
        exc.data,
    )
    return error_response(
        code=exc.code,
        message=exc.message,
        data=exc.data,
        status_code=exc.status_code,
    )


async def handle_validation_error(_: Request, exc: RequestValidationError):
    code, message = classify_validation_error(exc)
    logger.warning("请求校验失败: code=%s message=%s errors=%s", code, message, exc.errors())
    return error_response(
        code=code,
        message=message,
        data={"errors": exc.errors()},
        status_code=400,
    )


async def handle_http_error(_: Request, exc: HTTPException):
    code = classify_http_error_code(exc.status_code)
    logger.warning("HTTP 异常: http_status=%s code=%s detail=%s", exc.status_code, code, exc.detail)
    return error_response(
        code=code,
        message=str(exc.detail),
        data={},
        status_code=exc.status_code,
    )


async def handle_unexpected_error(_: Request, exc: Exception):
    logger.exception("未处理的服务端异常", exc_info=exc)
    return error_response(
        code=error_codes.INTERNAL_SERVER_ERROR,
        message="服务端内部错误",
        data={},
        status_code=500,
    )


def classify_validation_error(exc: RequestValidationError) -> tuple[int, str]:
    """将 FastAPI 校验错误映射到 PDF 定义的错误码。"""

    error_types = {error.get("type") for error in exc.errors()}

    if "json_invalid" in error_types:
        return error_codes.JSON_PARSE_ERROR, "请求体 JSON 解析失败"

    if "missing" in error_types:
        return error_codes.MISSING_REQUIRED_FIELD, "必填字段缺失"

    return error_codes.INVALID_FIELD_VALUE, "字段值不合法"


def classify_http_error_code(status_code: int) -> int:
    """将 HTTP 异常映射到 PDF 定义的错误码。"""

    if status_code == 429:
        return error_codes.RATE_LIMIT_EXCEEDED

    if status_code == 503:
        return error_codes.STORAGE_SERVICE_UNAVAILABLE

    if 400 <= status_code < 500:
        return error_codes.INVALID_FIELD_VALUE

    return error_codes.INTERNAL_SERVER_ERROR


def register_exception_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, handle_app_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(HTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unexpected_error)
