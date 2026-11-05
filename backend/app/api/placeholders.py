"""已注册但尚未实现的路由辅助函数。"""

from __future__ import annotations

from typing import NoReturn

from backend.app.api import error_codes
from backend.app.api.errors import AppError


def raise_not_implemented(capability: str) -> NoReturn:
    raise AppError(
        message=f"{capability}尚未实现",
        code=error_codes.INTERNAL_SERVER_ERROR,
        status_code=500,
        data={"status": "待实现"},
    )
