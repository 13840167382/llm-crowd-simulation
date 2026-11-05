"""实验拓扑 ORM 模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.domain.enums import ExitType, ZoneType
from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, CreatedAtMixin
from backend.app.infrastructure.db.models.enums import build_postgres_enum

zone_type_enum = build_postgres_enum(ZoneType, name="zone_type")
exit_type_enum = build_postgres_enum(ExitType, name="exit_type")


class ExperimentZone(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """实验拓扑中的区域定义。"""

    __tablename__ = "experiment_zones"
    __table_args__ = (
        UniqueConstraint("experiment_id", "zone_id", name="uk_experiment_zones_experiment_id_zone_id"),
        Index("idx_experiment_zones_experiment_id_zone_type", "experiment_id", "zone_type"),
        Index("idx_experiment_zones_experiment_id_is_entry_zone", "experiment_id", "is_entry_zone"),
        {"comment": "实验拓扑中的区域定义"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外区域编号")
    zone_name: Mapped[str] = mapped_column(String(64), nullable=False, comment="区域名称")
    zone_type: Mapped[ZoneType] = mapped_column(zone_type_enum, nullable=False, comment="区域类型")
    min_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="边界最小 X")
    min_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="边界最小 Z")
    max_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="边界最大 X")
    max_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="边界最大 Z")
    capacity: Mapped[int] = mapped_column(Integer, nullable=False, comment="区域容量")
    is_entry_zone: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="是否入口区域",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="扩展字段",
    )


class ExperimentExit(Base, BigIntPrimaryKeyMixin, CreatedAtMixin):
    """实验拓扑中的出口定义。"""

    __tablename__ = "experiment_exits"
    __table_args__ = (
        ForeignKeyConstraint(
            ["experiment_id", "source_zone_id"],
            ["experiment_zones.experiment_id", "experiment_zones.zone_id"],
            name="fk_experiment_exits_source_zone",
        ),
        ForeignKeyConstraint(
            ["experiment_id", "target_zone_id"],
            ["experiment_zones.experiment_id", "experiment_zones.zone_id"],
            name="fk_experiment_exits_target_zone",
        ),
        UniqueConstraint("experiment_id", "exit_id", name="uk_experiment_exits_experiment_id_exit_id"),
        Index("idx_experiment_exits_experiment_id_source_zone_id", "experiment_id", "source_zone_id"),
        Index("idx_experiment_exits_experiment_id_target_zone_id", "experiment_id", "target_zone_id"),
        {"comment": "实验拓扑中的出口关系"},
    )

    experiment_id: Mapped[int] = mapped_column(
        ForeignKey("experiments.id"),
        nullable=False,
        comment="所属实验主键",
    )
    exit_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="对外出口编号")
    source_zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="源区域编号")
    target_zone_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="目标区域编号")
    exit_type: Mapped[ExitType] = mapped_column(exit_type_enum, nullable=False, comment="出口类型")
    position_x: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="出口 X 坐标")
    position_z: Mapped[float] = mapped_column(Numeric(10, 3), nullable=False, comment="出口 Z 坐标")
    pass_capacity_per_sec: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        comment="每秒通行能力",
    )
    metadata_json: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="扩展字段",
    )
