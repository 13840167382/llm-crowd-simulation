"""数据库枚举列定义。"""

from __future__ import annotations

from enum import Enum

from sqlalchemy.types import Enum as SAEnum


def _enum_values(enum_cls: type[Enum]) -> list[str]:
    """提取枚举值列表，用于 PostgreSQL 原生枚举。"""

    return [member.value for member in enum_cls]


def build_postgres_enum(enum_cls: type[Enum], name: str) -> SAEnum:
    """构建统一配置的 PostgreSQL 枚举列。"""

    return SAEnum(
        enum_cls,
        name=name,
        native_enum=True,
        validate_strings=True,
        values_callable=_enum_values,
    )
