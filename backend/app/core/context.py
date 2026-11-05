"""请求级上下文辅助函数。"""

from __future__ import annotations

from contextvars import ContextVar, Token

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
experiment_id_var: ContextVar[str | None] = ContextVar("experiment_id", default=None)


def set_request_context(*, request_id: str, experiment_id: str | None) -> tuple[Token[str], Token[str | None]]:
    """在一次请求进入时写入上下文，供日志和链路追踪复用。"""

    return request_id_var.set(request_id), experiment_id_var.set(experiment_id)


def reset_request_context(tokens: tuple[Token[str], Token[str | None]]) -> None:
    """在请求结束时恢复上下文，避免串请求污染。"""

    request_id_token, experiment_id_token = tokens
    request_id_var.reset(request_id_token)
    experiment_id_var.reset(experiment_id_token)


def get_request_id() -> str:
    """获取当前请求上下文中的 request_id。"""

    return request_id_var.get()


def get_experiment_id() -> str | None:
    """获取当前请求上下文中的 experiment_id。"""

    return experiment_id_var.get()
