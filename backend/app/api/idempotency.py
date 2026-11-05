"""基于 X-Request-Id 的通用请求幂等组件。"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Callable
from uuid import uuid4

from fastapi.responses import JSONResponse

from backend.app.api import error_codes
from backend.app.api.errors import AppError
from backend.app.core.config import get_settings
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


def execute_idempotent_request(
    *,
    request_id: str | None,
    operation: str,
    fingerprint_data: dict[str, Any],
    handler: Callable[[], JSONResponse],
) -> JSONResponse:
    """对带显式 `X-Request-Id` 的写请求执行通用幂等控制。

    幂等只在 Redis 启用且客户端传入 request_id 时生效。组件会缓存完整 JSONResponse，
    并用 fingerprint_data 的哈希确保同一个 request_id 不能被复用于不同请求体。
    """

    if not request_id:
        return handler()

    settings = get_settings()
    if not settings.redis_enabled:
        logger.info(
            "X-Request-Id 幂等降级跳过: operation=%s request_id=%s reason=redis_disabled",
            operation,
            request_id,
        )
        return handler()

    redis_client = get_redis_client()
    request_hash = _build_request_hash(fingerprint_data)
    response_cache_parts = ("idempotency", "response", operation, request_id)
    lock_parts = ("idempotency", "lock", operation, request_id)

    # 先查响应快照，命中时无需再获取锁，直接回放历史响应。
    cached_snapshot = _load_cached_snapshot(redis_client, response_cache_parts)
    if cached_snapshot is not None:
        return _build_replay_response(
            cached_snapshot=cached_snapshot,
            request_id=request_id,
            request_hash=request_hash,
            operation=operation,
        )

    lock_token = str(uuid4())
    # 再用短 TTL 锁保护正在处理中的请求，避免两次相同写操作同时进入业务层。
    acquired = redis_client.set_if_absent(
        *lock_parts,
        value=lock_token,
        ttl_seconds=settings.request_idempotency_lock_ttl_seconds,
    )
    if not acquired:
        cached_snapshot = _load_cached_snapshot(redis_client, response_cache_parts)
        if cached_snapshot is not None:
            return _build_replay_response(
                cached_snapshot=cached_snapshot,
                request_id=request_id,
                request_hash=request_hash,
                operation=operation,
            )
        logger.warning(
            "X-Request-Id 幂等锁冲突: operation=%s request_id=%s request_hash=%s",
            operation,
            request_id,
            request_hash,
        )
        raise AppError(
            message=f"请求 `{request_id}` 正在处理中，请稍后重试",
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=409,
            data={
                "request_id": request_id,
                "operation": operation,
                "reason": "request_processing",
            },
        )

    try:
        response = handler()
        snapshot = _build_response_snapshot(response=response, request_hash=request_hash)
        if snapshot is not None:
            redis_client.set_json(
                *response_cache_parts,
                value=snapshot,
                ttl_seconds=settings.request_idempotency_ttl_seconds,
            )
            logger.info(
                "X-Request-Id 幂等快照写入完成: operation=%s request_id=%s status=%s",
                operation,
                request_id,
                response.status_code,
            )
        return response
    finally:
        redis_client.delete_if_value(*lock_parts, expected_value=lock_token)


def _build_request_hash(fingerprint_data: dict[str, Any]) -> str:
    """把请求关键信息序列化成稳定摘要，避免同一请求号对应不同请求体。"""

    serialized = json.dumps(
        fingerprint_data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _load_cached_snapshot(redis_client: object, cache_parts: tuple[str, ...]) -> dict[str, Any] | None:
    """读取 Redis 中缓存的历史响应快照。"""

    if not hasattr(redis_client, "get_json"):
        return None
    cached = redis_client.get_json(*cache_parts)
    return cached if isinstance(cached, dict) else None


def _build_replay_response(
    *,
    cached_snapshot: dict[str, Any],
    request_id: str,
    request_hash: str,
    operation: str,
) -> JSONResponse:
    """命中历史快照时直接回放响应，同时校验请求内容未变化。"""

    cached_request_hash = str(cached_snapshot.get("request_hash") or "")
    if cached_request_hash and cached_request_hash != request_hash:
        logger.warning(
            "X-Request-Id 命中请求体冲突: operation=%s request_id=%s cached_hash=%s request_hash=%s",
            operation,
            request_id,
            cached_request_hash,
            request_hash,
        )
        raise AppError(
            message=f"请求 `{request_id}` 已被用于其他请求内容，请更换新的 X-Request-Id",
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=409,
            data={
                "request_id": request_id,
                "operation": operation,
                "reason": "idempotency_payload_mismatch",
            },
        )

    payload = cached_snapshot.get("payload")
    status_code = int(cached_snapshot.get("status_code") or 200)
    if not isinstance(payload, dict):
        raise AppError(
            message="幂等响应快照格式无效",
            code=error_codes.INTERNAL_SERVER_ERROR,
            status_code=500,
            data={},
        )

    logger.info(
        "X-Request-Id 幂等回放命中: operation=%s request_id=%s status=%s",
        operation,
        request_id,
        status_code,
    )
    return JSONResponse(status_code=status_code, content=payload)


def _build_response_snapshot(*, response: JSONResponse, request_hash: str) -> dict[str, Any] | None:
    """从 JSONResponse 中提取可回放的响应内容。"""

    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, json.JSONDecodeError):
        logger.warning("X-Request-Id 幂等快照写入被跳过: response_body_unavailable")
        return None

    if not isinstance(payload, dict):
        logger.warning("X-Request-Id 幂等快照写入被跳过: response_payload_not_dict")
        return None

    return {
        "request_hash": request_hash,
        "status_code": response.status_code,
        "payload": payload,
    }
