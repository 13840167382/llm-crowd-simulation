"""人群类型模板 ORM 模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import CHAR, Boolean, Index, Integer, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, TimestampMixin


class CrowdTypeTemplate(Base, BigIntPrimaryKeyMixin, TimestampMixin):
    """全局人群类型模板定义。"""

    __tablename__ = "crowd_type_templates"
    __table_args__ = (
        UniqueConstraint("type_id", name="uk_crowd_type_templates_type_id"),
        Index("idx_crowd_type_templates_builtin", "is_builtin"),
        {"comment": "全局人群类型模板定义"},
    )

    type_id: Mapped[str] = mapped_column(String(32), nullable=False, comment="对外模板标识")
    display_name: Mapped[str] = mapped_column(String(32), nullable=False, comment="显示名称")
    color_hex: Mapped[str] = mapped_column(CHAR(7), nullable=False, comment="颜色值")
    is_builtin: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否系统内置",
    )
    base_speed: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False, comment="基础速度")
    social_distance: Mapped[float] = mapped_column(
        Numeric(4, 2),
        nullable=False,
        comment="社交距离",
    )
    tolerance_duration_ms: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="容忍时长，毫秒",
    )
    attraction_weights: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        comment="功能区吸引权重",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="扩展字段",
    )
