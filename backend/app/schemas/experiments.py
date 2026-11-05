"""实验相关 Schema 定义。"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import Field, StringConstraints

from backend.app.domain.enums import CompletionReason, ExperimentStatus
from backend.app.schemas.base import SchemaModel

ExperimentId = Annotated[str, StringConstraints(min_length=1, max_length=64)]
ExperimentName = Annotated[str, StringConstraints(min_length=1, max_length=128)]
MapId = Annotated[str, StringConstraints(min_length=1, max_length=64)]


class LLMConfig(SchemaModel):
    """LLM 相关配置。"""

    model: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        ...,
        description="模型名称",
    )
    temperature: float = Field(..., ge=0.0, le=2.0, description="采样温度")
    max_tokens: int = Field(..., ge=1, description="单次调用最大输出 token 数")
    timeout_ms: int = Field(..., ge=1, description="LLM 调用超时时间，毫秒")


class DecisionConfig(SchemaModel):
    """批量决策相关配置。"""

    batch_interval_ms: int = Field(..., ge=1, description="决策批次周期，毫秒")
    batch_size: int = Field(..., ge=1, description="单批次 agent 数量")
    fallback_to_algorithm: bool = Field(..., description="是否允许算法兜底")


class ExperimentCreateRequest(SchemaModel):
    """创建实验请求。"""

    name: ExperimentName = Field(..., description="实验名称")
    description: str | None = Field(default=None, description="实验描述")
    map_id: MapId | None = Field(default=None, description="地图标识")
    llm_config: LLMConfig = Field(..., description="LLM 配置")
    decision_config: DecisionConfig = Field(..., description="决策批次配置")
    tags: list[str] = Field(default_factory=list, description="实验标签")


class ExperimentCreatedData(SchemaModel):
    """创建实验响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    status: ExperimentStatus = Field(..., description="当前实验状态")
    created_at_ms: int = Field(..., ge=0, description="创建时间戳，毫秒")


class ExperimentStartRequest(SchemaModel):
    """启动实验请求。"""

    actual_start_time_ms: int | None = Field(
        default=None,
        ge=0,
        description="Unity 端实际模拟起始时间，毫秒",
    )


class ExperimentStartedData(SchemaModel):
    """启动实验响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    status: ExperimentStatus = Field(..., description="当前实验状态")
    started_at_ms: int = Field(..., ge=0, description="实验启动时间戳，毫秒")


class ExperimentCompleteRequest(SchemaModel):
    """结束实验请求。"""

    reason: CompletionReason = Field(..., description="实验完成原因")
    actual_duration_ms: int = Field(..., ge=0, description="实际运行时长，毫秒")
    final_agent_count: int = Field(..., ge=0, description="结束时 agent 总数")


class CrowdTypeCompletionStat(SchemaModel):
    """实验完成摘要中的单类人群统计。"""

    type_id: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="人群类型模板标识",
    )
    final_count: int = Field(..., ge=0, description="结束时该类型剩余人数")
    exited_count: int = Field(..., ge=0, description="该类型已离场人数")


class ExperimentCompletionSummary(SchemaModel):
    """实验完成摘要。"""

    total_trajectory_points: int = Field(..., ge=0, description="轨迹点总数")
    total_decision_requests: int = Field(..., ge=0, description="决策请求批次数")
    total_llm_decisions: int = Field(..., ge=0, description="LLM 决策总数")
    llm_fallback_count: int = Field(..., ge=0, description="LLM fallback 次数")
    duration_ms: int = Field(..., ge=0, description="实验持续时间，毫秒")
    crowd_type_stats: list[CrowdTypeCompletionStat] = Field(
        default_factory=list,
        description="按人群类型拆分的实验完成统计",
    )


class ExperimentCompletedData(SchemaModel):
    """结束实验响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    status: ExperimentStatus = Field(..., description="当前实验状态")
    summary: ExperimentCompletionSummary = Field(..., description="实验运行摘要")


class ExperimentFailRequest(SchemaModel):
    """标记实验失败请求。"""

    reason: Annotated[str, StringConstraints(min_length=1, max_length=128)] = Field(
        ...,
        description="失败原因标识",
    )
    message: Annotated[str, StringConstraints(max_length=1024)] | None = Field(
        default=None,
        description="失败原因说明",
    )
    failed_at_ms: int | None = Field(
        default=None,
        ge=0,
        description="失败发生时间戳，毫秒；未传时由服务端推导",
    )
    actual_duration_ms: int | None = Field(
        default=None,
        ge=0,
        description="失败前实际运行时长，毫秒",
    )
    final_agent_count: int | None = Field(
        default=None,
        ge=0,
        description="失败时 agent 总数",
    )
    details: dict[str, Any] = Field(default_factory=dict, description="失败附加信息")


class ExperimentFailureSummary(SchemaModel):
    """实验失败摘要。"""

    reason: str = Field(..., description="失败原因标识")
    message: str | None = Field(default=None, description="失败原因说明")
    failed_at_ms: int = Field(..., ge=0, description="失败发生时间戳，毫秒")
    duration_ms: int | None = Field(default=None, ge=0, description="失败前实际运行时长，毫秒")
    final_agent_count: int | None = Field(default=None, ge=0, description="失败时 agent 总数")
    previous_status: ExperimentStatus = Field(..., description="失败前实验状态")
    details: dict[str, Any] = Field(default_factory=dict, description="失败附加信息")


class ExperimentFailedData(SchemaModel):
    """标记实验失败响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    status: ExperimentStatus = Field(..., description="当前实验状态")
    failed_at_ms: int = Field(..., ge=0, description="失败发生时间戳，毫秒")
    summary: ExperimentFailureSummary = Field(..., description="实验失败摘要")
