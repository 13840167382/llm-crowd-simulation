"""轨迹相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from backend.app.domain.enums import AgentState
from backend.app.schemas.base import SchemaModel
from backend.app.schemas.crowd_types import TypeId
from backend.app.schemas.decisions import AgentId, BatchId
from backend.app.schemas.topology import ExitId, Position2D, ZoneId


class TrajectoryPointIngestItem(SchemaModel):
    """单个轨迹采样点。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    position: Position2D = Field(..., description="当前位置")
    zone_id: ZoneId = Field(..., description="当前所在区域")
    speed: float = Field(..., ge=0.0, description="当前速度")
    state: AgentState = Field(..., description="当前行为状态")
    target_exit_id: ExitId | None = Field(default=None, description="当前目标出口")
    is_queuing: bool = Field(default=False, description="是否处于排队状态")


class TrajectoryBatchRequest(SchemaModel):
    """批量轨迹上报请求。"""

    batch_id: BatchId = Field(..., description="批次唯一标识")
    simulation_time_ms: int = Field(..., ge=0, description="当前模拟时间，毫秒")
    sample_interval_ms: int = Field(..., ge=1, description="采样间隔，毫秒")
    points: list[TrajectoryPointIngestItem] = Field(..., min_length=1, description="轨迹采样点列表")


class TrajectoryBatchResponseData(SchemaModel):
    """批量轨迹上报响应数据。"""

    batch_id: BatchId = Field(..., description="批次唯一标识")
    accepted_count: int = Field(..., ge=0, description="接收成功点数")
    rejected_count: int = Field(..., ge=0, description="拒绝点数")
    total_stored_points: int = Field(..., ge=0, description="累计已存储轨迹点数")
