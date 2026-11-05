"""决策相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints

from backend.app.domain.enums import DecisionSource
from backend.app.schemas.base import SchemaModel
from backend.app.schemas.crowd_types import TypeId
from backend.app.schemas.topology import ExitId, ZoneId

AgentId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
BatchId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class AvailableExitOption(SchemaModel):
    """单个可选出口说明。"""

    exit_id: ExitId = Field(..., description="候选出口标识")
    target_zone_id: ZoneId = Field(..., description="出口到达的目标区域")
    algorithm_score: float = Field(..., description="算法给出的推荐评分")
    nl_description: str = Field(..., description="自然语言描述")


class DecisionAgentRequest(SchemaModel):
    """单个 agent 的决策请求。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    current_zone_id: ZoneId = Field(..., description="当前所在区域")
    stay_duration_ms: int = Field(..., ge=0, description="当前区域停留时长，毫秒")
    patience_ratio: float = Field(..., ge=0.0, description="耐心消耗比例")
    algorithm_recommendation: ExitId | None = Field(default=None, description="算法推荐出口")
    dilemma_summary: str = Field(..., description="当前困境摘要")
    available_exits: list[AvailableExitOption] = Field(
        ...,
        min_length=1,
        description="当前可选出口列表",
    )


class DecisionBatchRequest(SchemaModel):
    """批量决策请求。"""

    batch_id: BatchId = Field(..., description="批次唯一标识")
    simulation_time_ms: int = Field(..., ge=0, description="当前模拟时间，毫秒")
    agents: list[DecisionAgentRequest] = Field(..., min_length=1, description="待决策 agent 列表")


class DecisionResultItem(SchemaModel):
    """单个 agent 的决策结果。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    chosen_exit_id: ExitId | None = Field(default=None, description="最终选择的出口")
    decision_source: DecisionSource = Field(..., description="决策来源")
    reasoning: str | None = Field(default=None, description="决策理由")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="决策置信度")


class DecisionBatchResponseData(SchemaModel):
    """批量决策响应数据。"""

    batch_id: BatchId = Field(..., description="批次唯一标识")
    processed_at_ms: int = Field(..., ge=0, description="处理完成时间，毫秒")
    processing_duration_ms: int = Field(..., ge=0, description="本批处理耗时，毫秒")
    total_count: int = Field(..., ge=0, description="本批请求总数")
    success_count: int = Field(..., ge=0, description="成功决策数量")
    fallback_count: int = Field(..., ge=0, description="fallback 数量")
    decisions: list[DecisionResultItem] = Field(default_factory=list, description="决策结果列表")
