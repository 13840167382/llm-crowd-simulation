"""人群分配与出生点相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, StringConstraints, field_validator

from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import DistributionMode
from backend.app.schemas.base import SchemaModel
from backend.app.schemas.crowd_types import ColorHex, TypeId
from backend.app.schemas.experiments import ExperimentId, ExperimentStatus
from backend.app.schemas.topology import Position2D, ZoneId

AgentIdPrefix = Annotated[str, StringConstraints(min_length=1, max_length=32)]
AgentId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class ZoneWeight(SchemaModel):
    """区域权重项。"""

    zone_id: ZoneId = Field(..., description="目标区域标识")
    weight: float = Field(..., gt=0.0, le=1.0, description="区域分配权重")


class SpawnPointWeight(SchemaModel):
    """出生点扩散中心及其权重。"""

    x: float = Field(..., description="出生点中心 X 坐标")
    z: float = Field(..., description="出生点中心 Z 坐标")
    weight: float = Field(..., gt=0.0, le=1.0, description="该出生点权重")

    @field_validator("x", "z", mode="before")
    @classmethod
    def normalize_coordinate(cls, value):
        if value is None:
            return value
        return round_coordinate(value)


class DistributionBase(SchemaModel):
    """初始分布公共字段。"""

    spread_radius: float | None = Field(
        default=None,
        gt=0.0,
        description="在区域或中心点周围扩散的半径，单位米",
    )
    avoid_exits: bool = Field(default=True, description="是否避开出口附近生成")


class ZoneWeightedDistribution(DistributionBase):
    """按区域权重分配。"""

    mode: Literal["zone_weighted"] = Field(default="zone_weighted", description="分布模式")
    zone_weights: list[ZoneWeight] = Field(..., min_length=1, description="区域权重配置")


class RandomEntryDistribution(DistributionBase):
    """随机分配到所有入口区域。"""

    mode: Literal["random_entry"] = Field(default="random_entry", description="分布模式")


class PointSpreadDistribution(DistributionBase):
    """按指定坐标点扩散分配。"""

    mode: Literal["point_spread"] = Field(default="point_spread", description="分布模式")
    spawn_points: list[SpawnPointWeight] = Field(..., min_length=1, description="出生点权重配置")


class ZoneFixedDistribution(DistributionBase):
    """固定在单个区域内分配。"""

    mode: Literal["zone_fixed"] = Field(default="zone_fixed", description="分布模式")
    fixed_zone_id: ZoneId = Field(..., description="固定生成的区域标识")


AllocationDistribution = Annotated[
    ZoneWeightedDistribution | RandomEntryDistribution | PointSpreadDistribution | ZoneFixedDistribution,
    Field(discriminator="mode"),
]


class CrowdAllocationGroup(SchemaModel):
    """单类人群分配配置。"""

    type_id: TypeId = Field(..., description="引用的人群类型模板标识")
    count: int = Field(..., ge=1, le=10000, description="该类人群数量")
    distribution: AllocationDistribution = Field(..., description="初始空间分布配置")


class CrowdAllocationRequest(SchemaModel):
    """配置人群分配请求。"""

    groups: list[CrowdAllocationGroup] = Field(..., min_length=1, description="人群分组配置列表")
    agent_id_prefix: AgentIdPrefix | None = Field(
        default=None,
        description="agent_id 前缀，不填时默认使用 experiment_id 前 8 位",
    )


class CrowdAllocationGroupSummary(SchemaModel):
    """单类人群分配摘要。"""

    type_id: TypeId = Field(..., description="人群类型模板标识")
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="显示名称",
    )
    color_hex: ColorHex = Field(..., description="颜色标识")
    count: int = Field(..., ge=0, description="人群数量")
    mode: DistributionMode = Field(..., description="分布模式")


class CrowdAllocationResponseData(SchemaModel):
    """配置人群分配响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    status: ExperimentStatus = Field(..., description="当前实验状态")
    total_count: int = Field(..., ge=0, description="总人数")
    group_count: int = Field(..., ge=0, description="分组数量")
    group_summary: list[CrowdAllocationGroupSummary] = Field(
        default_factory=list,
        description="分组摘要信息",
    )
    validation_errors: list[str] = Field(default_factory=list, description="分配校验错误列表")


class SpawnPositionsRequest(SchemaModel):
    """生成出生点请求。"""

    seed: int | None = Field(default=None, description="随机种子，不传则由后端生成")


class SpawnedAgent(SchemaModel):
    """出生点生成结果中的单个 agent。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    type_id: TypeId = Field(..., description="人群类型模板标识")
    zone_id: ZoneId = Field(..., description="出生区域标识")
    position: Position2D = Field(..., description="初始坐标")
    rotation_y: float | None = Field(default=None, description="初始朝向，单位度")


class SpawnPositionsResponseData(SchemaModel):
    """生成出生点响应数据。"""

    seed: int = Field(..., description="实际使用的随机种子")
    total_count: int = Field(..., ge=0, description="生成的总人数")
    agents: list[SpawnedAgent] = Field(default_factory=list, description="生成的 agent 列表")
    warnings: list[str] = Field(default_factory=list, description="位置生成过程中的警告信息")
