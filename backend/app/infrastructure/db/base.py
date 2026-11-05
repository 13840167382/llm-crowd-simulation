"""SQLAlchemy 基础模型与公共列定义。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Identity, MetaData, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_name)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """项目统一的 SQLAlchemy 基类。"""

    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class BigIntPrimaryKeyMixin:
    """使用 bigint 自增主键。"""

    id: Mapped[int] = mapped_column(
        BigInteger,
        Identity(start=1),
        primary_key=True,
        comment="内部主键",
    )


class CreatedAtMixin:
    """仅包含创建时间。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        comment="创建时间",
    )


class TimestampMixin(CreatedAtMixin):
    """通用创建时间与更新时间。"""

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
        comment="更新时间",
    )
