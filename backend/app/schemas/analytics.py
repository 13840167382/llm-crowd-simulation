"""分析查询相关 Schema 定义。"""

from __future__ import annotations

from enum import Enum
from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import AgentState, HeatmapMetric, NormalizationMode
from backend.app.schemas.base import SchemaModel
from backend.app.schemas.crowd_types import ColorHex, TypeId
from backend.app.schemas.decisions import AgentId, BatchId, DecisionSource
from backend.app.schemas.experiments import ExperimentId
from backend.app.schemas.topology import ExitId, Position2D, ZoneId

TrajectoryPointCompact = tuple[int, float, float, str, float, AgentState, bool]
PathTracePoint = tuple[int, float, float]


class QueryFormat(str, Enum):
    """轨迹查询返回格式枚举。"""

    RECORDS = "records"
    COMPACT = "compact"


class StatsQueryParams(SchemaModel):
    """实验统计概览查询参数。"""

    time_range_start_ms: int | None = Field(default=None, ge=0, description="统计起始时间")
    time_range_end_ms: int | None = Field(default=None, ge=0, description="统计结束时间")
    zone_id: ZoneId | None = Field(default=None, description="区域过滤")
    crowd_type: TypeId | None = Field(default=None, description="人群类型过滤")


class TrajectoriesQueryParams(SchemaModel):
    """轨迹数据查询参数。"""

    agent_id: AgentId | None = Field(default=None, description="精确查询指定人物")
    crowd_type: TypeId | None = Field(default=None, description="按人群类型过滤")
    zone_id: ZoneId | None = Field(default=None, description="按区域过滤")
    time_start_ms: int | None = Field(default=None, ge=0, description="时间范围起始")
    time_end_ms: int | None = Field(default=None, ge=0, description="时间范围结束")
    state: AgentState | None = Field(default=None, description="按行为状态过滤")
    is_queuing: bool | None = Field(default=None, description="是否仅查排队状态")
    page: int = Field(default=1, ge=1, description="页码")
    page_size: int = Field(default=1000, ge=1, le=10000, description="每页条数")
    format: QueryFormat = Field(default=QueryFormat.RECORDS, description="返回格式")


class AgentTrajectoryQueryParams(SchemaModel):
    """单人物轨迹详情查询参数。"""

    time_start_ms: int | None = Field(default=None, ge=0, description="时间范围起始")
    time_end_ms: int | None = Field(default=None, ge=0, description="时间范围结束")
    include_decisions: bool = Field(default=False, description="是否包含决策记录")


class PathTracesQueryParams(SchemaModel):
    """路径追踪查询参数。"""

    crowd_type: TypeId = Field(..., description="指定人群类型")
    sample_rate: int = Field(default=1, ge=1, le=10, description="采样率，每 N 个点取 1 个")
    time_start_ms: int | None = Field(default=None, ge=0, description="时间范围起始")
    time_end_ms: int | None = Field(default=None, ge=0, description="时间范围结束")
    max_agents: int = Field(default=100, ge=1, le=500, description="最多返回多少人的轨迹")


class AllocationSnapshotGroup(SchemaModel):
    """统计接口中的人群分配快照项。"""

    type_id: TypeId = Field(..., description="人群类型模板标识")
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="显示名称",
    )
    color_hex: ColorHex = Field(..., description="颜色标识")
    count: int = Field(..., ge=0, description="该类人数")


class AllocationSnapshot(SchemaModel):
    """实验启动时的人群分配快照。"""

    groups: list[AllocationSnapshotGroup] = Field(default_factory=list, description="人群分组快照")
    total_count: int = Field(..., ge=0, description="总人数")


class TimeRangeData(SchemaModel):
    """时间范围数据。"""

    start_ms: int = Field(..., ge=0, description="起始时间，毫秒")
    end_ms: int = Field(..., ge=0, description="结束时间，毫秒")


class CrowdTypeAgentStats(SchemaModel):
    """某一类人群的总体统计。"""

    count: int = Field(..., ge=0, description="总人数")
    active: int = Field(..., ge=0, description="当前活跃人数")
    exited: int = Field(..., ge=0, description="已离开人数")
    avg_speed: float = Field(..., ge=0.0, description="平均速度")
    avg_stay_ms: int = Field(..., ge=0, description="平均停留时长，毫秒")


class AgentStatsSummary(SchemaModel):
    """实验整体人物统计。"""

    total_count: int = Field(..., ge=0, description="总人数")
    current_active_count: int = Field(..., ge=0, description="当前活跃人数")
    exited_count: int = Field(..., ge=0, description="已离开人数")
    by_crowd_type: dict[str, CrowdTypeAgentStats] = Field(
        default_factory=dict,
        description="按人群类型聚合的统计",
    )


class ZoneCrowdTypeDistribution(SchemaModel):
    """区域内按人群类型拆分的统计。"""

    avg_count: float = Field(..., ge=0.0, description="平均人数")
    peak_count: int = Field(..., ge=0, description="峰值人数")


class ZoneStatsItem(SchemaModel):
    """单个区域的统计。"""

    zone_id: ZoneId = Field(..., description="区域标识")
    zone_name: Annotated[str, StringConstraints(min_length=1, max_length=64)] = Field(
        ...,
        description="区域名称",
    )
    peak_count: int = Field(..., ge=0, description="峰值人数")
    peak_time_ms: int = Field(..., ge=0, description="峰值时间，毫秒")
    avg_count: float = Field(..., ge=0.0, description="平均人数")
    avg_stay_ms: int = Field(..., ge=0, description="平均停留时长，毫秒")
    total_entries: int = Field(..., ge=0, description="总进入数")
    total_exits: int = Field(..., ge=0, description="总离开数")
    queue_peak_count: int = Field(..., ge=0, description="排队峰值人数")
    utilization_rate: float = Field(..., ge=0.0, le=1.0, description="区域利用率")
    by_crowd_type: dict[str, ZoneCrowdTypeDistribution] = Field(
        default_factory=dict,
        description="区域内按人群类型拆分的统计",
    )


class ExitStatsItem(SchemaModel):
    """单个出口的统计。"""

    exit_id: ExitId = Field(..., description="出口标识")
    total_passages: int = Field(..., ge=0, description="通行次数")
    avg_queue_length: float = Field(..., ge=0.0, description="平均队列长度")
    max_queue_length: int = Field(..., ge=0, description="最大队列长度")
    avg_wait_ms: int = Field(..., ge=0, description="平均等待时长，毫秒")
    max_wait_ms: int = Field(..., ge=0, description="最大等待时长，毫秒")
    throughput_per_min: float = Field(..., ge=0.0, description="每分钟吞吐量")


class DecisionStatsSummary(SchemaModel):
    """决策维度统计。"""

    total_batches: int = Field(..., ge=0, description="决策批次数")
    total_agents_decided: int = Field(..., ge=0, description="参与决策的 agent 总数")
    llm_success_count: int = Field(..., ge=0, description="LLM 成功次数")
    llm_fallback_count: int = Field(..., ge=0, description="LLM fallback 次数")
    llm_fallback_rate: float = Field(..., ge=0.0, le=1.0, description="LLM fallback 比率")
    avg_llm_latency_ms: int = Field(..., ge=0, description="平均 LLM 时延，毫秒")
    algorithm_override_count: int = Field(..., ge=0, description="推翻算法推荐次数")
    algorithm_override_rate: float = Field(..., ge=0.0, le=1.0, description="推翻算法推荐比率")
    override_by_crowd_type: dict[str, float] = Field(
        default_factory=dict,
        description="各人群类型的推翻算法推荐比率",
    )


class ExperimentStatsData(SchemaModel):
    """实验统计概览响应数据。"""

    experiment_id: ExperimentId = Field(..., description="实验唯一标识")
    allocation_snapshot: AllocationSnapshot = Field(..., description="人群分配快照")
    time_range: TimeRangeData = Field(..., description="统计时间范围")
    agent_stats: AgentStatsSummary = Field(..., description="人物总体统计")
    zone_stats: list[ZoneStatsItem] = Field(default_factory=list, description="区域统计列表")
    exit_stats: list[ExitStatsItem] = Field(default_factory=list, description="出口统计列表")
    decision_stats: DecisionStatsSummary = Field(..., description="决策统计")


class TrajectoryRecordItem(SchemaModel):
    """轨迹记录模式下的单条记录。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    timestamp_ms: int = Field(..., ge=0, description="采样时间，毫秒")
    position: Position2D = Field(..., description="采样位置")
    zone_id: ZoneId = Field(..., description="所在区域")
    speed: float = Field(..., ge=0.0, description="速度")
    state: AgentState = Field(..., description="行为状态")
    target_exit_id: ExitId | None = Field(default=None, description="目标出口")
    is_queuing: bool = Field(..., description="是否排队")


class TrajectoriesRecordsData(SchemaModel):
    """轨迹查询 records 模式响应数据。"""

    total_count: int = Field(..., ge=0, description="总记录数")
    page: int = Field(..., ge=1, description="当前页码")
    page_size: int = Field(..., ge=1, description="每页条数")
    total_pages: int = Field(..., ge=0, description="总页数")
    records: list[TrajectoryRecordItem] = Field(default_factory=list, description="轨迹记录列表")


class CompactTrajectoryAgentTrace(SchemaModel):
    """轨迹查询 compact 模式下的单个 agent 轨迹。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    points: list[TrajectoryPointCompact] = Field(default_factory=list, description="压缩轨迹点列表")


class TrajectoriesCompactData(SchemaModel):
    """轨迹查询 compact 模式响应数据。"""

    total_count: int = Field(..., ge=0, description="总记录数")
    page: int = Field(..., ge=1, description="当前页码")
    page_size: int = Field(..., ge=1, description="每页条数")
    total_pages: int = Field(..., ge=0, description="总页数")
    agents: list[CompactTrajectoryAgentTrace] = Field(default_factory=list, description="按 agent 聚合的轨迹")


class AgentDecisionDetail(SchemaModel):
    """单人物轨迹详情中的决策记录。"""

    batch_id: BatchId = Field(..., description="决策批次标识")
    simulation_time_ms: int = Field(..., ge=0, description="模拟时间，毫秒")
    current_zone_id: ZoneId = Field(..., description="当前区域")
    stay_duration_ms: int = Field(..., ge=0, description="停留时长，毫秒")
    patience_ratio: float = Field(..., ge=0.0, description="耐心消耗比例")
    chosen_exit_id: ExitId | None = Field(default=None, description="最终选择出口")
    decision_source: DecisionSource = Field(..., description="决策来源")
    reasoning: str | None = Field(default=None, description="决策理由")
    confidence: float | None = Field(default=None, ge=0.0, le=1.0, description="置信度")
    algorithm_recommendation: ExitId | None = Field(default=None, description="算法推荐出口")
    was_overridden: bool = Field(..., description="是否偏离算法推荐")


class AgentTrajectoryDetailData(SchemaModel):
    """单人物轨迹详情响应数据。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    crowd_type_display: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="人群类型显示名称",
    )
    crowd_type_color: ColorHex = Field(..., description="人群类型颜色标识")
    spawn_zone_id: ZoneId = Field(..., description="出生区域")
    spawn_position: Position2D = Field(..., description="出生位置")
    point_count: int = Field(..., ge=0, description="轨迹点数量")
    time_span_ms: int = Field(..., ge=0, description="覆盖时间跨度，毫秒")
    points: list[TrajectoryPointCompact] = Field(default_factory=list, description="压缩轨迹点列表")
    decisions: list[AgentDecisionDetail] = Field(default_factory=list, description="相关决策记录")


class GridOrigin(SchemaModel):
    """热力图网格原点。"""

    x: float = Field(..., description="原点 X 坐标")
    z: float = Field(..., description="原点 Z 坐标")

    @field_validator("x", "z", mode="before")
    @classmethod
    def normalize_coordinate(cls, value):
        if value is None:
            return value
        return round_coordinate(value)


class GridConfig(SchemaModel):
    """热力图网格配置。"""

    origin: GridOrigin = Field(..., description="网格原点")
    cell_size: float = Field(..., gt=0.0, description="单元格边长")
    cols: int = Field(..., ge=1, description="列数")
    rows: int = Field(..., ge=1, description="行数")


class HeatmapRequest(SchemaModel):
    """热力图生成请求。"""

    time_start_ms: int = Field(..., ge=0, description="起始时间，毫秒")
    time_end_ms: int = Field(..., ge=0, description="结束时间，毫秒")
    grid_config: GridConfig = Field(..., description="网格配置")
    metric: HeatmapMetric = Field(..., description="热力图指标")
    crowd_type_filter: TypeId | None = Field(default=None, description="人群类型过滤")
    zone_id_filter: ZoneId | None = Field(default=None, description="区域过滤")
    normalization: NormalizationMode = Field(..., description="归一化方式")


class HeatmapStatistics(SchemaModel):
    """热力图统计摘要。"""

    min_value: float = Field(..., description="归一化后最小值")
    max_value: float = Field(..., description="归一化后最大值")
    raw_min: float | None = Field(default=None, description="原始最小值")
    raw_max: float | None = Field(default=None, description="原始最大值")
    percentile_99_raw: float | None = Field(default=None, description="原始值 99 分位")
    non_empty_cells: int = Field(..., ge=0, description="非空单元格数量")
    total_cells: int = Field(..., ge=0, description="总单元格数量")


class HeatmapData(SchemaModel):
    """热力图响应数据。"""

    grid_config: GridConfig = Field(..., description="网格配置")
    metric: HeatmapMetric = Field(..., description="热力图指标")
    normalization: NormalizationMode = Field(..., description="归一化方式")
    crowd_type_filter: TypeId | None = Field(default=None, description="人群类型过滤")
    statistics: HeatmapStatistics = Field(..., description="统计摘要")
    cells: list[float] = Field(default_factory=list, description="网格单元格值，行优先排列")


class HeatmapCompareRequest(SchemaModel):
    """热力图对比请求。"""

    time_start_ms: int = Field(..., ge=0, description="起始时间，毫秒")
    time_end_ms: int = Field(..., ge=0, description="结束时间，毫秒")
    grid_config: GridConfig = Field(..., description="网格配置")
    metric: HeatmapMetric = Field(..., description="热力图指标")
    crowd_types: list[TypeId] = Field(..., min_length=1, max_length=6, description="需要对比的人群类型")
    normalization: NormalizationMode = Field(..., description="归一化方式")


class HeatmapCompareLayer(SchemaModel):
    """热力图对比中的单层数据。"""

    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="显示名称",
    )
    color_hex: ColorHex = Field(..., description="颜色标识")
    statistics: HeatmapStatistics = Field(..., description="统计摘要")
    cells: list[float] = Field(default_factory=list, description="网格单元格值")


class HeatmapCompareData(SchemaModel):
    """热力图对比响应数据。"""

    grid_config: GridConfig = Field(..., description="网格配置")
    metric: HeatmapMetric = Field(..., description="热力图指标")
    layers: list[HeatmapCompareLayer] = Field(default_factory=list, description="对比图层列表")


class PathTraceItem(SchemaModel):
    """单条路径追踪数据。"""

    agent_id: AgentId = Field(..., description="agent 唯一标识")
    point_count: int = Field(..., ge=0, description="轨迹点数量")
    points: list[PathTracePoint] = Field(default_factory=list, description="降采样后的路径点")


class PathTracesData(SchemaModel):
    """路径追踪响应数据。"""

    crowd_type: TypeId = Field(..., description="人群类型模板标识")
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=32)] = Field(
        ...,
        description="显示名称",
    )
    color_hex: ColorHex = Field(..., description="颜色标识")
    agent_count_in_response: int = Field(..., ge=0, description="响应中包含的 agent 数")
    total_agents_of_type: int = Field(..., ge=0, description="该类型总人数")
    sample_rate: int = Field(..., ge=1, description="实际使用的采样率")
    traces: list[PathTraceItem] = Field(default_factory=list, description="路径追踪列表")
