"""应用层通用辅助函数。"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from backend.app.api import error_codes
from backend.app.api.errors import AppError


def datetime_to_timestamp_ms(value: datetime) -> int:
    """将时间对象转换为 Unix 毫秒时间戳。"""

    return int(value.timestamp() * 1000)


def decimal_to_float(value: Decimal | float | int | None) -> float | None:
    """将 Decimal 等数值统一转换为 float。"""

    if value is None:
        return None
    return float(value)


def raise_experiment_not_found(message: str) -> None:
    """抛出实验不存在错误。"""

    raise AppError(
        message=message,
        code=error_codes.EXPERIMENT_NOT_FOUND,
        status_code=404,
    )


def raise_experiment_status_conflict(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出实验状态冲突错误。"""

    raise AppError(
        message=message,
        code=error_codes.EXPERIMENT_STATUS_CONFLICT,
        status_code=409,
        data=data,
    )


def raise_topology_validation_failed(message: str, validation_errors: list[str] | None = None) -> None:
    """抛出拓扑校验失败错误。"""

    raise AppError(
        message=message,
        code=error_codes.TOPOLOGY_VALIDATION_FAILED,
        status_code=400,
        data={"validation_errors": validation_errors or [message]},
    )


def raise_crowd_allocation_validation_failed(
    message: str,
    validation_errors: list[str] | None = None,
) -> None:
    """抛出人群分配校验失败错误。"""

    raise AppError(
        message=message,
        code=error_codes.CROWD_ALLOCATION_VALIDATION_FAILED,
        status_code=400,
        data={"validation_errors": validation_errors or [message]},
    )


def raise_crowd_type_not_found(message: str) -> None:
    """抛出人群模板不存在错误。"""

    raise AppError(
        message=message,
        code=error_codes.CROWD_TYPE_TEMPLATE_NOT_FOUND,
        status_code=404,
    )


def raise_crowd_type_conflict(message: str) -> None:
    """抛出人群模板标识冲突错误。"""

    raise AppError(
        message=message,
        code=error_codes.CROWD_TYPE_ID_CONFLICT,
        status_code=409,
    )


def raise_crowd_type_referenced(message: str) -> None:
    """抛出人群模板仍被引用错误。"""

    raise AppError(
        message=message,
        code=error_codes.CROWD_TYPE_REFERENCED,
        status_code=400,
    )


def raise_batch_size_exceeded(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出批次大小超限错误。"""

    raise AppError(
        message=message,
        code=error_codes.BATCH_SIZE_EXCEEDED,
        status_code=400,
        data=data,
    )


def raise_invalid_field_value(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出字段值非法错误。"""

    raise AppError(
        message=message,
        code=error_codes.INVALID_FIELD_VALUE,
        status_code=400,
        data=data,
    )


def raise_llm_call_failed(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出单个人物级别的 LLM 调用失败错误。"""

    raise AppError(
        message=message,
        code=error_codes.LLM_CALL_FAILED,
        status_code=500,
        data=data,
    )


def raise_llm_batch_timeout(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出批量 LLM 决策整体超时错误。"""

    raise AppError(
        message=message,
        code=error_codes.LLM_BATCH_TIMEOUT,
        status_code=504,
        data=data,
    )


def raise_llm_response_parse_failed(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出 LLM 响应解析失败错误。"""

    raise AppError(
        message=message,
        code=error_codes.LLM_RESPONSE_PARSE_FAILED,
        status_code=500,
        data=data,
    )


def raise_llm_service_unavailable(message: str, data: dict[str, Any] | None = None) -> None:
    """抛出 LLM 服务不可用错误。"""

    raise AppError(
        message=message,
        code=error_codes.LLM_SERVICE_UNAVAILABLE,
        status_code=503,
        data=data,
    )
