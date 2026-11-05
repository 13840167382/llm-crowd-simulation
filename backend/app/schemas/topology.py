"""拓扑相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, StringConstraints, field_validator, model_validator

from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import ExitType, ZoneType
from backend.app.schemas.base import SchemaModel

ZoneId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ExitId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class Bounds2D(SchemaModel):
    """二维边界框。"""

    min_x: float = Field(..., description="边界最小 X")
    min_z: float = Field(..., description="边界最小 Z")
    max_x: float = Field(..., description="边界最大 X")
    max_z: float = Field(..., description="边界最大 Z")

    @field_validator("min_x", "min_z", "max_x", "max_z", mode="before")
    @classmethod
    def normalize_coordinate(cls, value):
        if value is None:
            return value
        return round_coordinate(value)

    @model_validator(mode="after")
    def validate_bounds(self) -> "Bounds2D":
        """确保边界最小值不大于最大值。"""

        if self.min_x > self.max_x or self.min_z > self.max_z:
            raise ValueError("bounds 的最小值不能大于最大值")
        return self


class Position2D(SchemaModel):
    """二维坐标点。"""

    x: float = Field(..., description="X 坐标")
    z: float = Field(..., description="Z 坐标")

    @field_validator("x", "z", mode="before")
    @classmethod
    def normalize_coordinate(cls, value):
        if value is None:
            return value
        return round_coordinate(value)


class TopologyZone(SchemaModel):
    """拓扑区域定义。"""

    zone_id: ZoneId = Field(..., description="区域唯一标识")
    zone_name: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        ...,
        description="区域名称",
    )
    zone_type: ZoneType = Field(..., description="区域类型")
    bounds: Bounds2D = Field(..., description="区域边界")
    capacity: int = Field(..., ge=1, description="区域容量")
    is_entry_zone: bool = Field(default=False, description="是否为入口区域")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


class TopologyExit(SchemaModel):
    """拓扑出口定义。"""

    exit_id: ExitId = Field(..., description="出口唯一标识")
    source_zone_id: ZoneId = Field(..., description="源区域标识")
    target_zone_id: ZoneId = Field(..., description="目标区域标识")
    exit_type: ExitType = Field(..., description="出口类型")
    position: Position2D = Field(..., description="出口坐标")
    pass_capacity_per_sec: int = Field(..., ge=1, description="每秒通行能力")
    metadata: dict[str, Any] = Field(default_factory=dict, description="扩展字段")


class TopologyUpsertRequest(SchemaModel):
    """提交实验拓扑请求。"""

    zones: list[TopologyZone] = Field(..., min_length=1, description="区域列表")
    exits: list[TopologyExit] = Field(default_factory=list, description="出口列表")


class TopologyUpsertResponseData(SchemaModel):
    """提交实验拓扑响应数据。"""

    zone_count: int = Field(..., ge=0, description="区域数量")
    exit_count: int = Field(..., ge=0, description="出口数量")
    entry_zones: list[ZoneId] = Field(default_factory=list, description="入口区域标识列表")
    validation_errors: list[str] = Field(default_factory=list, description="拓扑校验错误列表")
