"""分析查询应用服务。

本模块是前端统计看板、轨迹回放、单人详情、路径追踪和热力图的读取入口。
运行中的实验优先读原始轨迹/决策数据以保证实时性；实验结束后优先使用分钟聚合、
1 秒采样轨迹和热力图缓存来降低查询成本。维护任务清理原始运行数据后，仍能依赖
聚合表、采样表和已预热热力图提供主要分析能力。
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
import hashlib
import json
from math import ceil, log1p

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.api import error_codes
from backend.app.api.errors import AppError
from backend.app.application.common import decimal_to_float
from backend.app.application.analytics.aggregation import bucket_start_ms
from backend.app.application.experiments.service import get_experiment_or_404
from backend.app.core.config import get_settings
from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import DecisionSource, ExperimentStatus
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    DecisionMetric1m,
    DecisionBatch,
    ExitMetric1m,
    ExperimentAgent,
    ExperimentAllocation,
    ExperimentExit,
    ExperimentZone,
    HeatmapCache,
    TrajectoryPoint,
    TrajectoryPointSampled1s,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.repositories import (
    ExperimentAllocationRepository,
    ExperimentAgentRepository,
    ExperimentExitRepository,
    ExperimentZoneRepository,
    HeatmapCacheRepository,
)
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.analytics import (
    AgentDecisionDetail,
    AgentStatsSummary,
    AgentTrajectoryDetailData,
    AgentTrajectoryQueryParams,
    AllocationSnapshot,
    AllocationSnapshotGroup,
    CompactTrajectoryAgentTrace,
    CrowdTypeAgentStats,
    DecisionStatsSummary,
    ExperimentStatsData,
    ExitStatsItem,
    GridConfig,
    GridOrigin,
    HeatmapCompareData,
    HeatmapCompareLayer,
    HeatmapCompareRequest,
    HeatmapData,
    HeatmapMetric,
    HeatmapRequest,
    HeatmapStatistics,
    NormalizationMode,
    PathTraceItem,
    PathTracesData,
    PathTracesQueryParams,
    QueryFormat,
    TimeRangeData,
    TrajectoriesCompactData,
    TrajectoriesQueryParams,
    TrajectoriesRecordsData,
    TrajectoryRecordItem,
    ZoneCrowdTypeDistribution,
    ZoneStatsItem,
)
from backend.app.schemas.topology import Position2D

logger = get_logger(__name__)


def get_experiment_stats(
    session: Session,
    *,
    experiment_id: str,
    params: object,
) -> ExperimentStatsData:
    """获取实验统计概览。

    已结束实验如果聚合表完整，则直接从 *_metric_1m 表计算统计；运行中的实验或
    聚合缺失时回退原始轨迹点和决策明细。这样既保证实时联调能看到最新数据，也保证
    清理运行数据后的历史实验仍有轻量统计来源。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    stats_params = params if hasattr(params, "time_range_start_ms") else None
    if stats_params is None:
        raise AppError("统计参数不合法", code=error_codes.INVALID_FIELD_VALUE, status_code=400)

    allocations = ExperimentAllocationRepository(session).list_by_experiment_id(experiment.id)
    allocation_snapshot, _ = _build_allocation_snapshot(allocations)
    spawn_agents = ExperimentAgentRepository(session).list_by_experiment_id(experiment.id)
    zones = ExperimentZoneRepository(session).list_by_experiment_id(experiment.id)
    exits = ExperimentExitRepository(session).list_by_experiment_id(experiment.id)
    aggregate_time_range = _resolve_time_range(
        session,
        experiment_pk=experiment.id,
        experiment_started_at_ms=experiment.simulation_started_at_ms,
        experiment_completed_at_ms=experiment.simulation_completed_at_ms,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    zone_metric_rows = _list_zone_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        zone_id=stats_params.zone_id,
        time_start_ms=aggregate_time_range.start_ms,
        time_end_ms=aggregate_time_range.end_ms,
    )
    zone_metric_type_rows = (
        _list_zone_metric_rows(
            session,
            experiment_pk=experiment.id,
            crowd_type="__all_types__",
            zone_id=stats_params.zone_id,
            time_start_ms=aggregate_time_range.start_ms,
            time_end_ms=aggregate_time_range.end_ms,
        )
        if stats_params.crowd_type is None
        else []
    )
    exit_metric_rows = _list_exit_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        time_start_ms=aggregate_time_range.start_ms,
        time_end_ms=aggregate_time_range.end_ms,
    )
    decision_metric_rows = _list_decision_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        time_start_ms=aggregate_time_range.start_ms,
        time_end_ms=aggregate_time_range.end_ms,
    )
    decision_metric_type_rows = (
        _list_decision_metric_rows(
            session,
            experiment_pk=experiment.id,
            crowd_type="__all_types__",
            time_start_ms=aggregate_time_range.start_ms,
            time_end_ms=aggregate_time_range.end_ms,
        )
        if stats_params.crowd_type is None
        else []
    )
    agent_metric_rows = _list_agent_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        time_start_ms=aggregate_time_range.start_ms,
        time_end_ms=aggregate_time_range.end_ms,
    )
    agent_metric_type_rows = (
        _list_agent_metric_rows(
            session,
            experiment_pk=experiment.id,
            crowd_type="__all_types__",
            time_start_ms=aggregate_time_range.start_ms,
            time_end_ms=aggregate_time_range.end_ms,
        )
        if stats_params.crowd_type is None
        else []
    )
    # RUNNING 实验仍在不断写入轨迹/决策明细，优先读原始表避免聚合延迟造成看板滞后。
    prefer_raw_runtime = experiment.status == ExperimentStatus.RUNNING
    if (not prefer_raw_runtime) and zone_metric_rows and exit_metric_rows and decision_metric_rows and agent_metric_rows:
        # 聚合行齐全时走轻量路径，尤其适合已完成或已归档实验。
        return ExperimentStatsData(
            experiment_id=experiment.experiment_id,
            allocation_snapshot=allocation_snapshot,
            time_range=aggregate_time_range,
            agent_stats=_build_agent_stats_from_aggregates(
                spawn_agents=spawn_agents,
                rows=agent_metric_rows,
                type_rows=agent_metric_type_rows,
                crowd_type_filter=stats_params.crowd_type,
            ),
            zone_stats=_build_zone_stats_from_aggregates(
                zones=zones,
                rows=zone_metric_rows,
                type_rows=zone_metric_type_rows,
                crowd_type_filter=stats_params.crowd_type,
                zone_id_filter=stats_params.zone_id,
            ),
            exit_stats=_build_exit_stats_from_aggregates(
                exits=exits,
                rows=exit_metric_rows,
                time_range=aggregate_time_range,
            ),
            decision_stats=_build_decision_stats_from_aggregates(
                rows=decision_metric_rows,
                type_rows=decision_metric_type_rows,
                total_batches=_count_decision_batches_in_range(
                    session,
                    experiment_pk=experiment.id,
                    time_start_ms=aggregate_time_range.start_ms,
                    time_end_ms=aggregate_time_range.end_ms,
                ),
            ),
        )
    trajectory_points = _list_filtered_trajectory_points(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        zone_id=stats_params.zone_id,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    decision_rows = _list_filtered_decisions(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        zone_id=stats_params.zone_id,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    # 聚合缺失或运行中实验会回源原始明细；下方仍尽量复用已有聚合行来减少全量扫描。
    time_range = _build_time_range(
        experiment_started_at_ms=experiment.simulation_started_at_ms,
        experiment_completed_at_ms=experiment.simulation_completed_at_ms,
        points=trajectory_points,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )

    zone_metric_rows = _list_zone_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        zone_id=stats_params.zone_id,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    zone_metric_type_rows = (
        _list_zone_metric_rows(
            session,
            experiment_pk=experiment.id,
            crowd_type="__all_types__",
            zone_id=stats_params.zone_id,
            time_start_ms=stats_params.time_range_start_ms,
            time_end_ms=stats_params.time_range_end_ms,
        )
        if stats_params.crowd_type is None
        else []
    )
    exit_metric_rows = _list_exit_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    decision_metric_rows = _list_decision_metric_rows(
        session,
        experiment_pk=experiment.id,
        crowd_type=stats_params.crowd_type,
        time_start_ms=stats_params.time_range_start_ms,
        time_end_ms=stats_params.time_range_end_ms,
    )
    decision_metric_type_rows = (
        _list_decision_metric_rows(
            session,
            experiment_pk=experiment.id,
            crowd_type="__all_types__",
            time_start_ms=stats_params.time_range_start_ms,
            time_end_ms=stats_params.time_range_end_ms,
        )
        if stats_params.crowd_type is None
        else []
    )

    return ExperimentStatsData(
        experiment_id=experiment.experiment_id,
        allocation_snapshot=allocation_snapshot,
        time_range=time_range,
        agent_stats=_build_agent_stats(
            spawn_agents=spawn_agents,
            points=trajectory_points,
            decisions=decision_rows,
            crowd_type_filter=stats_params.crowd_type,
        ),
        zone_stats=(
            _build_zone_stats_from_aggregates(
                zones=zones,
                rows=zone_metric_rows,
                type_rows=zone_metric_type_rows,
                crowd_type_filter=stats_params.crowd_type,
                zone_id_filter=stats_params.zone_id,
            )
            if zone_metric_rows
            else _build_zone_stats(
                zones=zones,
                points=trajectory_points,
                decisions=decision_rows,
                crowd_type_filter=stats_params.crowd_type,
                zone_id_filter=stats_params.zone_id,
            )
        ),
        exit_stats=(
            _build_exit_stats_from_aggregates(
                exits=exits,
                rows=exit_metric_rows,
                time_range=time_range,
            )
            if exit_metric_rows
            else _build_exit_stats(
                exits=exits,
                points=trajectory_points,
                decisions=decision_rows,
                time_range=time_range,
            )
        ),
        decision_stats=(
            _build_decision_stats_from_aggregates(
                rows=decision_metric_rows,
                type_rows=decision_metric_type_rows,
                total_batches=len({decision.decision_batch_id for decision in decision_rows}),
            )
            if decision_metric_rows
            else _build_decision_stats(decision_rows)
        ),
    )


def get_trajectories(
    session: Session,
    *,
    experiment_id: str,
    params: object,
) -> TrajectoriesRecordsData | TrajectoriesCompactData:
    """按条件查询轨迹数据。

    非运行实验的 compact 查询或未指定 agent_id 的大范围查询优先读 1 秒采样表，
    避免一次回放拉出过多原始点。采样表没有命中时，再按原始轨迹查询限制回源。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    query_params = params if hasattr(params, "page") else None
    if query_params is None:
        raise AppError("轨迹查询参数不合法", code=error_codes.INVALID_FIELD_VALUE, status_code=400)

    # 采样表面向历史回放和大范围查询；运行中的实验仍读原始表以保证最新轨迹可见。
    use_sampled_points = (
        query_params.format == QueryFormat.COMPACT or query_params.agent_id is None
    ) and experiment.status != ExperimentStatus.RUNNING
    if use_sampled_points:
        sampled_filters = _build_sampled_trajectory_filters(
            experiment_pk=experiment.id,
            agent_id=query_params.agent_id,
            crowd_type=query_params.crowd_type,
            zone_id=query_params.zone_id,
            time_start_ms=query_params.time_start_ms,
            time_end_ms=query_params.time_end_ms,
            state=query_params.state,
            is_queuing=query_params.is_queuing,
        )
        total_count = _count_sampled_trajectory_points(session, sampled_filters)
        page = query_params.page
        page_size = query_params.page_size
        offset = (page - 1) * page_size
        points = _list_sampled_trajectory_points_page(session, sampled_filters, offset=offset, limit=page_size)
        total_pages = ceil(total_count / page_size) if total_count else 0

        if total_count == 0:
            # 老数据或维护任务未生成采样点时，保留原始表兜底，但仍执行窗口/分页保护。
            _validate_raw_trajectory_query(query_params)
            raw_filters = _build_trajectory_filters(
                experiment_pk=experiment.id,
                agent_id=query_params.agent_id,
                crowd_type=query_params.crowd_type,
                zone_id=query_params.zone_id,
                time_start_ms=query_params.time_start_ms,
                time_end_ms=query_params.time_end_ms,
                state=query_params.state,
                is_queuing=query_params.is_queuing,
            )
            total_count = _count_trajectory_points(session, raw_filters)
            points = _list_trajectory_points_page(session, raw_filters, offset=offset, limit=page_size)
            total_pages = ceil(total_count / page_size) if total_count else 0

            if query_params.format == QueryFormat.COMPACT:
                grouped_points: dict[str, list[tuple[int, float, float, str, float, object, bool]]] = defaultdict(list)
                crowd_type_by_agent: dict[str, str] = {}
                for point in points:
                    grouped_points[point.agent_id].append(_build_compact_point(point))
                    crowd_type_by_agent[point.agent_id] = point.crowd_type

                return TrajectoriesCompactData(
                    total_count=total_count,
                    page=page,
                    page_size=page_size,
                    total_pages=total_pages,
                    agents=[
                        CompactTrajectoryAgentTrace(
                            agent_id=agent_id,
                            crowd_type=crowd_type_by_agent[agent_id],
                            points=agent_points,
                        )
                        for agent_id, agent_points in grouped_points.items()
                    ],
                )

            return TrajectoriesRecordsData(
                total_count=total_count,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                records=[_build_trajectory_record(point) for point in points],
            )

        if query_params.format == QueryFormat.COMPACT:
            grouped_points: dict[str, list[tuple[int, float, float, str, float, object, bool]]] = defaultdict(list)
            crowd_type_by_agent: dict[str, str] = {}
            for point in points:
                grouped_points[point.agent_id].append(_build_compact_point(point))
                crowd_type_by_agent[point.agent_id] = point.crowd_type

            return TrajectoriesCompactData(
                total_count=total_count,
                page=page,
                page_size=page_size,
                total_pages=total_pages,
                agents=[
                    CompactTrajectoryAgentTrace(
                        agent_id=agent_id,
                        crowd_type=crowd_type_by_agent[agent_id],
                        points=agent_points,
                    )
                    for agent_id, agent_points in grouped_points.items()
                ],
            )

        return TrajectoriesRecordsData(
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            records=[_build_trajectory_record(point) for point in points],
        )

    _validate_raw_trajectory_query(query_params)

    filters = _build_trajectory_filters(
        experiment_pk=experiment.id,
        agent_id=query_params.agent_id,
        crowd_type=query_params.crowd_type,
        zone_id=query_params.zone_id,
        time_start_ms=query_params.time_start_ms,
        time_end_ms=query_params.time_end_ms,
        state=query_params.state,
        is_queuing=query_params.is_queuing,
    )
    total_count = _count_trajectory_points(session, filters)
    page = query_params.page
    page_size = query_params.page_size
    offset = (page - 1) * page_size
    points = _list_trajectory_points_page(session, filters, offset=offset, limit=page_size)
    total_pages = ceil(total_count / page_size) if total_count else 0

    if query_params.format == QueryFormat.COMPACT:
        grouped_points: dict[str, list[tuple[int, float, float, str, float, object, bool]]] = defaultdict(list)
        crowd_type_by_agent: dict[str, str] = {}
        for point in points:
            grouped_points[point.agent_id].append(_build_compact_point(point))
            crowd_type_by_agent[point.agent_id] = point.crowd_type

        # compact 模式按 agent 聚合，减少大批量回放时的响应体体积。
        return TrajectoriesCompactData(
            total_count=total_count,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            agents=[
                CompactTrajectoryAgentTrace(
                    agent_id=agent_id,
                    crowd_type=crowd_type_by_agent[agent_id],
                    points=agent_points,
                )
                for agent_id, agent_points in grouped_points.items()
            ],
        )

    return TrajectoriesRecordsData(
        total_count=total_count,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        records=[_build_trajectory_record(point) for point in points],
    )


def get_agent_trajectory_detail(
    session: Session,
    *,
    experiment_id: str,
    agent_id: str,
    params: object,
) -> AgentTrajectoryDetailData:
    """获取单个体轨迹详情。

    单人物详情用于调试和回放定位问题，因此直接读原始轨迹点，并可按需附带该人物的
    决策历史。原始数据被清理后，这类精细详情不再可完整恢复。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    query_params = params if hasattr(params, "include_decisions") else None
    if query_params is None:
        raise AppError("个体轨迹查询参数不合法", code=error_codes.INVALID_FIELD_VALUE, status_code=400)

    spawn_agent = session.scalar(
        select(ExperimentAgent).where(
            ExperimentAgent.experiment_id == experiment.id,
            ExperimentAgent.agent_id == agent_id,
        )
    )
    if spawn_agent is None:
        raise AppError(
            message=f"实验 `{experiment.experiment_id}` 中不存在个体 `{agent_id}`",
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=404,
        )

    allocations = ExperimentAllocationRepository(session).list_by_experiment_id(experiment.id)
    _, snapshot_map = _build_allocation_snapshot(allocations)
    snapshot = snapshot_map.get(
        spawn_agent.crowd_type,
        {"display_name": spawn_agent.crowd_type, "color_hex": "#999999"},
    )

    filters = _build_trajectory_filters(
        experiment_pk=experiment.id,
        agent_id=agent_id,
        crowd_type=None,
        zone_id=None,
        time_start_ms=query_params.time_start_ms,
        time_end_ms=query_params.time_end_ms,
        state=None,
        is_queuing=None,
    )
    points = _list_all_trajectory_points(session, filters)

    decisions: list[AgentDecisionDetail] = []
    if query_params.include_decisions:
        decision_rows = session.execute(
            select(AgentDecision, DecisionBatch.batch_id)
            .join(DecisionBatch, DecisionBatch.id == AgentDecision.decision_batch_id)
            .where(
                AgentDecision.experiment_id == experiment.id,
                AgentDecision.agent_id == agent_id,
                *(
                    [AgentDecision.simulation_time_ms >= query_params.time_start_ms]
                    if query_params.time_start_ms is not None
                    else []
                ),
                *(
                    [AgentDecision.simulation_time_ms <= query_params.time_end_ms]
                    if query_params.time_end_ms is not None
                    else []
                ),
            )
            .order_by(AgentDecision.simulation_time_ms, AgentDecision.id)
        ).all()
        decisions = [_build_agent_decision_detail(row[0], row[1]) for row in decision_rows]

    first_point_time = points[0].simulation_time_ms if points else 0
    last_point_time = points[-1].simulation_time_ms if points else 0

    return AgentTrajectoryDetailData(
        agent_id=spawn_agent.agent_id,
        crowd_type=spawn_agent.crowd_type,
        crowd_type_display=str(snapshot["display_name"]),
        crowd_type_color=str(snapshot["color_hex"]),
        spawn_zone_id=spawn_agent.spawn_zone_id,
        spawn_position=Position2D(
            x=decimal_to_float(spawn_agent.spawn_x) or 0.0,
            z=decimal_to_float(spawn_agent.spawn_z) or 0.0,
        ),
        point_count=len(points),
        time_span_ms=max(last_point_time - first_point_time, 0),
        points=[_build_compact_point(point) for point in points],
        decisions=decisions,
    )


def create_heatmap(
    session: Session,
    *,
    experiment_id: str,
    payload: HeatmapRequest,
) -> HeatmapData:
    """生成单层热力图数据。

    热力图查询统一走 _get_or_create_heatmap：先读 Redis 热缓存，再读数据库缓存，
    最后才从轨迹点回源计算并写回缓存。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    data, cache_hit = _get_or_create_heatmap(
        session,
        experiment_pk=experiment.id,
        external_experiment_id=experiment.experiment_id,
        runtime_data_cleaned=_was_runtime_data_cleaned(experiment),
        time_start_ms=payload.time_start_ms,
        time_end_ms=payload.time_end_ms,
        grid_config=payload.grid_config,
        metric=payload.metric,
        crowd_type=payload.crowd_type_filter,
        zone_id=payload.zone_id_filter,
        normalization=payload.normalization,
    )
    logger.info(
        "热力图请求完成: experiment_id=%s metric=%s cache_hit=%s crowd_type=%s zone_id=%s non_empty_cells=%s",
        experiment.experiment_id,
        payload.metric,
        cache_hit,
        payload.crowd_type_filter,
        payload.zone_id_filter,
        data.statistics.non_empty_cells,
    )
    return data


def compare_heatmap(
    session: Session,
    *,
    experiment_id: str,
    payload: HeatmapCompareRequest,
) -> HeatmapCompareData:
    """生成人群类型对比热力图。

    对比图本质上是多个 crowd_type 的单层热力图组合，因此每一层都复用同一套缓存键。
    这样单层接口和对比接口互相预热，不会重复计算相同的网格数据。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    allocations = ExperimentAllocationRepository(session).list_by_experiment_id(experiment.id)
    _, snapshot_map = _build_allocation_snapshot(allocations)
    layers: list[HeatmapCompareLayer] = []
    cache_hit_count = 0

    for crowd_type in payload.crowd_types:
        # 对比图层复用单层热力图缓存，避免不同入口各算一遍相同数据。
        heatmap_data, cache_hit = _get_or_create_heatmap(
            session,
            experiment_pk=experiment.id,
            external_experiment_id=experiment.experiment_id,
            runtime_data_cleaned=_was_runtime_data_cleaned(experiment),
            time_start_ms=payload.time_start_ms,
            time_end_ms=payload.time_end_ms,
            grid_config=payload.grid_config,
            metric=payload.metric,
            crowd_type=crowd_type,
            zone_id=None,
            normalization=payload.normalization,
        )
        cache_hit_count += int(cache_hit)
        snapshot = snapshot_map.get(crowd_type, {"display_name": crowd_type, "color_hex": "#999999"})
        layers.append(
            HeatmapCompareLayer(
                crowd_type=crowd_type,
                display_name=str(snapshot["display_name"]),
                color_hex=str(snapshot["color_hex"]),
                statistics=heatmap_data.statistics,
                cells=heatmap_data.cells,
            )
        )

    logger.info(
        "对比热力图生成完成: experiment_id=%s layer_count=%s cache_hits=%s",
        experiment.experiment_id,
        len(layers),
        cache_hit_count,
    )
    return HeatmapCompareData(
        grid_config=payload.grid_config,
        metric=payload.metric,
        layers=layers,
    )


def prewarm_experiment_heatmaps(
    session: Session,
    *,
    experiment_id: str,
) -> dict[str, object]:
    """为实验生成默认热力图缓存。

    维护任务会在清理原始轨迹前调用这里，提前生成全体人群和各 crowd_type 的默认热力图。
    如果清理后某个热力图没有缓存，就无法再从原始轨迹回源计算。
    """

    settings = get_settings()
    experiment = get_experiment_or_404(session, experiment_id)
    time_range = _get_heatmap_time_range(session, experiment_pk=experiment.id)
    if time_range is None:
        logger.info("热力图预热跳过: experiment_id=%s reason=no_trajectory_points", experiment.experiment_id)
        return {
            "experiment_id": experiment.experiment_id,
            "prewarmed_count": 0,
            "cache_hit_count": 0,
            "time_start_ms": None,
            "time_end_ms": None,
            "grid_config": None,
            "crowd_types": [],
            "requests": [],
        }

    zones = ExperimentZoneRepository(session).list_by_experiment_id(experiment.id)
    grid_config = _build_default_heatmap_grid_config(
        zones=zones,
        cell_size=max(settings.heatmap_prewarm_cell_size, 1.0),
    )
    allocations = ExperimentAllocationRepository(session).list_by_experiment_id(experiment.id)
    crowd_types = [
        allocation.type_id
        for allocation in allocations
        if allocation.count > 0
    ][: max(settings.heatmap_prewarm_max_crowd_types, 0)]

    requests: list[dict[str, object]] = []
    cache_hit_count = 0
    for crowd_type in [None, *crowd_types]:
        data, cache_hit = _get_or_create_heatmap(
            session,
            experiment_pk=experiment.id,
            external_experiment_id=experiment.experiment_id,
            runtime_data_cleaned=False,
            time_start_ms=time_range[0],
            time_end_ms=time_range[1],
            grid_config=grid_config,
            metric=HeatmapMetric.PASSAGE_COUNT,
            crowd_type=crowd_type,
            zone_id=None,
            normalization=NormalizationMode.MIN_MAX,
        )
        cache_hit_count += int(cache_hit)
        requests.append(
            {
                "crowd_type": crowd_type,
                "metric": HeatmapMetric.PASSAGE_COUNT.value,
                "normalization": NormalizationMode.MIN_MAX.value,
                "non_empty_cells": data.statistics.non_empty_cells,
                "cache_hit": cache_hit,
            }
        )

    logger.info(
        "实验热力图预热完成: experiment_id=%s prewarmed_count=%s cache_hit_count=%s crowd_type_count=%s",
        experiment.experiment_id,
        len(requests),
        cache_hit_count,
        len(crowd_types),
    )
    return {
        "experiment_id": experiment.experiment_id,
        "prewarmed_count": len(requests),
        "cache_hit_count": cache_hit_count,
        "time_start_ms": time_range[0],
        "time_end_ms": time_range[1],
        "grid_config": grid_config.model_dump(mode="json"),
        "crowd_types": crowd_types,
        "requests": requests,
    }


def get_path_traces(
    session: Session,
    *,
    experiment_id: str,
    params: object,
) -> PathTracesData:
    """获取按人群类型筛选后的路径追踪数据。"""

    experiment = get_experiment_or_404(session, experiment_id)
    query_params = params if hasattr(params, "crowd_type") else None
    if query_params is None:
        raise AppError("路径追踪参数不合法", code=error_codes.INVALID_FIELD_VALUE, status_code=400)

    spawn_agents = ExperimentAgentRepository(session).list_by_experiment_id(experiment.id)
    all_target_agents = [
        agent
        for agent in spawn_agents
        if agent.crowd_type == query_params.crowd_type
    ]
    target_agents = all_target_agents[: query_params.max_agents]
    allocations = ExperimentAllocationRepository(session).list_by_experiment_id(experiment.id)
    _, snapshot_map = _build_allocation_snapshot(allocations)
    snapshot = snapshot_map.get(
        query_params.crowd_type,
        {"display_name": query_params.crowd_type, "color_hex": "#999999"},
    )

    points: list[TrajectoryPointSampled1s] | list[TrajectoryPoint]
    points = []
    if experiment.status != ExperimentStatus.RUNNING:
        points = _list_sampled_trajectory_points_for_agents(
            session,
            experiment_pk=experiment.id,
            agent_ids=[agent.agent_id for agent in target_agents],
            crowd_type=query_params.crowd_type,
            time_start_ms=query_params.time_start_ms,
            time_end_ms=query_params.time_end_ms,
        )
    if not points:
        points = _list_trajectory_points_for_agents(
            session,
            experiment_pk=experiment.id,
            agent_ids=[agent.agent_id for agent in target_agents],
            crowd_type=query_params.crowd_type,
            time_start_ms=query_params.time_start_ms,
            time_end_ms=query_params.time_end_ms,
        )
    points_by_agent = _group_points_by_agent(points)

    traces: list[PathTraceItem] = []
    for agent in target_agents:
        agent_points = points_by_agent.get(agent.agent_id, [])
        if not agent_points:
            continue
        traces.append(
            PathTraceItem(
                agent_id=agent.agent_id,
                point_count=len(agent_points),
                points=_sample_path_trace_points(agent_points, query_params.sample_rate),
            )
        )
        if len(traces) >= query_params.max_agents:
            break

    logger.info(
        "路径追踪查询完成: experiment_id=%s crowd_type=%s trace_count=%s total_agents=%s",
        experiment.experiment_id,
        query_params.crowd_type,
        len(traces),
        len(all_target_agents),
    )
    return PathTracesData(
        crowd_type=query_params.crowd_type,
        display_name=str(snapshot["display_name"]),
        color_hex=str(snapshot["color_hex"]),
        agent_count_in_response=len(traces),
        total_agents_of_type=len(all_target_agents),
        sample_rate=query_params.sample_rate,
        traces=traces,
    )


def _build_allocation_snapshot(
    allocations: list[ExperimentAllocation],
) -> tuple[AllocationSnapshot, dict[str, dict[str, object]]]:
    groups = [
        AllocationSnapshotGroup(
            type_id=allocation.type_id,
            display_name=str(allocation.template_snapshot["display_name"]),
            color_hex=str(allocation.template_snapshot["color_hex"]),
            count=allocation.count,
        )
        for allocation in allocations
    ]
    snapshot_map = {allocation.type_id: dict(allocation.template_snapshot) for allocation in allocations}
    return (
        AllocationSnapshot(
            groups=groups,
            total_count=sum(group.count for group in groups),
        ),
        snapshot_map,
    )


def _build_time_range(
    *,
    experiment_started_at_ms: int | None,
    experiment_completed_at_ms: int | None,
    points: list[TrajectoryPoint],
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> TimeRangeData:
    if points:
        start_ms = int(points[0].simulation_time_ms)
        end_ms = int(points[-1].simulation_time_ms)
    else:
        start_ms = time_start_ms if time_start_ms is not None else int(experiment_started_at_ms or 0)
        if time_end_ms is not None:
            end_ms = time_end_ms
        elif experiment_completed_at_ms is not None:
            end_ms = int(experiment_completed_at_ms)
        else:
            end_ms = start_ms

    if time_start_ms is not None:
        start_ms = time_start_ms
    if time_end_ms is not None:
        end_ms = time_end_ms

    if end_ms < start_ms:
        end_ms = start_ms

    return TimeRangeData(start_ms=start_ms, end_ms=end_ms)


def _resolve_time_range(
    session: Session,
    *,
    experiment_pk: int,
    experiment_started_at_ms: int | None,
    experiment_completed_at_ms: int | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> TimeRangeData:
    if time_start_ms is not None or time_end_ms is not None:
        return _build_time_range(
            experiment_started_at_ms=experiment_started_at_ms,
            experiment_completed_at_ms=experiment_completed_at_ms,
            points=[],
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
        )

    point_stmt = select(
        func.min(TrajectoryPoint.simulation_time_ms),
        func.max(TrajectoryPoint.simulation_time_ms),
    ).where(TrajectoryPoint.experiment_id == experiment_pk)
    point_range = session.execute(point_stmt).one()
    if point_range[0] is not None and point_range[1] is not None:
        return TimeRangeData(start_ms=int(point_range[0]), end_ms=int(point_range[1]))

    sampled_stmt = select(
        func.min(TrajectoryPointSampled1s.simulation_time_ms),
        func.max(TrajectoryPointSampled1s.simulation_time_ms),
    ).where(TrajectoryPointSampled1s.experiment_id == experiment_pk)
    sampled_range = session.execute(sampled_stmt).one()
    if sampled_range[0] is not None and sampled_range[1] is not None:
        return TimeRangeData(start_ms=int(sampled_range[0]), end_ms=int(sampled_range[1]))

    start_ms = int(experiment_started_at_ms or 0)
    end_ms = int(experiment_completed_at_ms or start_ms)
    if end_ms < start_ms:
        end_ms = start_ms
    return TimeRangeData(start_ms=start_ms, end_ms=end_ms)


def _build_agent_stats(
    *,
    spawn_agents: list[ExperimentAgent],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
    crowd_type_filter: str | None,
) -> AgentStatsSummary:
    filtered_agents = [
        agent for agent in spawn_agents if crowd_type_filter is None or agent.crowd_type == crowd_type_filter
    ]
    active_agent_ids = {point.agent_id for point in points}
    point_speeds_by_type: dict[str, list[float]] = defaultdict(list)
    decision_stays_by_type: dict[str, list[int]] = defaultdict(list)
    active_agent_ids_by_type: dict[str, set[str]] = defaultdict(set)

    for point in points:
        point_speeds_by_type[point.crowd_type].append(decimal_to_float(point.speed) or 0.0)
        active_agent_ids_by_type[point.crowd_type].add(point.agent_id)

    for decision in decisions:
        decision_stays_by_type[decision.crowd_type].append(int(decision.stay_duration_ms))

    by_crowd_type: dict[str, CrowdTypeAgentStats] = {}
    agents_by_type: dict[str, list[ExperimentAgent]] = defaultdict(list)
    for agent in filtered_agents:
        agents_by_type[agent.crowd_type].append(agent)

    for crowd_type, agents in agents_by_type.items():
        count = len(agents)
        active = len(active_agent_ids_by_type.get(crowd_type, set()))
        avg_speed = _safe_average(point_speeds_by_type.get(crowd_type, []))
        avg_stay_ms = int(round(_safe_average(decision_stays_by_type.get(crowd_type, []))))
        by_crowd_type[crowd_type] = CrowdTypeAgentStats(
            count=count,
            active=active,
            exited=max(count - active, 0),
            avg_speed=avg_speed,
            avg_stay_ms=avg_stay_ms,
        )

    total_count = len(filtered_agents)
    current_active_count = len(active_agent_ids)
    return AgentStatsSummary(
        total_count=total_count,
        current_active_count=current_active_count,
        exited_count=max(total_count - current_active_count, 0),
        by_crowd_type=by_crowd_type,
    )


def _build_agent_stats_from_aggregates(
    *,
    spawn_agents: list[ExperimentAgent],
    rows: list[AgentMetric1m],
    type_rows: list[AgentMetric1m],
    crowd_type_filter: str | None,
) -> AgentStatsSummary:
    filtered_agents = [
        agent for agent in spawn_agents if crowd_type_filter is None or agent.crowd_type == crowd_type_filter
    ]
    total_count = len(filtered_agents)
    total_count_by_type: dict[str, int] = defaultdict(int)
    for agent in filtered_agents:
        total_count_by_type[agent.crowd_type] += 1

    latest_row = max(rows, key=lambda item: int(item.bucket_start_ms)) if rows else None
    current_active_count = int(latest_row.active_count) if latest_row is not None else 0
    exited_count = int(latest_row.exited_count) if latest_row is not None else max(total_count - current_active_count, 0)

    rows_by_type: dict[str, list[AgentMetric1m]] = defaultdict(list)
    for row in rows:
        if row.crowd_type is not None:
            rows_by_type[row.crowd_type].append(row)
    for row in type_rows:
        if row.crowd_type is not None:
            rows_by_type[row.crowd_type].append(row)

    by_crowd_type: dict[str, CrowdTypeAgentStats] = {}
    for crowd_type, count in total_count_by_type.items():
        crowd_rows = rows_by_type.get(crowd_type, [])
        latest_crowd_row = max(crowd_rows, key=lambda item: int(item.bucket_start_ms)) if crowd_rows else None
        by_crowd_type[crowd_type] = CrowdTypeAgentStats(
            count=count,
            active=int(latest_crowd_row.active_count) if latest_crowd_row is not None else 0,
            exited=int(latest_crowd_row.exited_count) if latest_crowd_row is not None else count,
            avg_speed=decimal_to_float(getattr(latest_crowd_row, "avg_speed", 0)) or 0.0,
            avg_stay_ms=int(getattr(latest_crowd_row, "avg_stay_ms", 0) or 0),
        )

    return AgentStatsSummary(
        total_count=total_count,
        current_active_count=current_active_count,
        exited_count=exited_count,
        by_crowd_type=by_crowd_type,
    )


def _build_zone_stats(
    *,
    zones: list[ExperimentZone],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
    crowd_type_filter: str | None,
    zone_id_filter: str | None,
) -> list[ZoneStatsItem]:
    target_zones = [zone for zone in zones if zone_id_filter is None or zone.zone_id == zone_id_filter]
    point_counts_by_zone_time: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    queue_counts_by_zone_time: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    point_counts_by_zone_type_time: dict[str, dict[str, dict[int, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    stay_values_by_zone: dict[str, list[int]] = defaultdict(list)
    entries_by_zone: dict[str, int] = defaultdict(int)
    exits_by_zone: dict[str, int] = defaultdict(int)

    for point in points:
        simulation_time_ms = int(point.simulation_time_ms)
        point_counts_by_zone_time[point.zone_id][simulation_time_ms] += 1
        point_counts_by_zone_type_time[point.zone_id][point.crowd_type][simulation_time_ms] += 1
        if point.is_queuing:
            queue_counts_by_zone_time[point.zone_id][simulation_time_ms] += 1

    for decision in decisions:
        stay_values_by_zone[decision.current_zone_id].append(int(decision.stay_duration_ms))

    for agent_points in _group_points_by_agent(points).values():
        # 通过相邻轨迹点是否跨区，近似推断进入/离开次数。
        for previous, current in zip(agent_points, agent_points[1:]):
            if previous.zone_id == current.zone_id:
                continue
            exits_by_zone[previous.zone_id] += 1
            entries_by_zone[current.zone_id] += 1

    zone_stats: list[ZoneStatsItem] = []
    for zone in target_zones:
        counts_by_time = point_counts_by_zone_time.get(zone.zone_id, {})
        queue_by_time = queue_counts_by_zone_time.get(zone.zone_id, {})
        if counts_by_time:
            peak_time_ms, peak_count = min(
                ((time_ms, count) for time_ms, count in counts_by_time.items()),
                key=lambda item: (-item[1], item[0]),
            )
            avg_count = _safe_average(list(counts_by_time.values()))
        else:
            peak_time_ms = 0
            peak_count = 0
            avg_count = 0.0

        zone_type_stats = {
            crowd_type: ZoneCrowdTypeDistribution(
                avg_count=_safe_average(list(type_counts.values())),
                peak_count=max(type_counts.values()) if type_counts else 0,
            )
            for crowd_type, type_counts in point_counts_by_zone_type_time.get(zone.zone_id, {}).items()
            if crowd_type_filter is None or crowd_type == crowd_type_filter
        }

        zone_stats.append(
            ZoneStatsItem(
                zone_id=zone.zone_id,
                zone_name=zone.zone_name,
                peak_count=peak_count,
                peak_time_ms=peak_time_ms,
                avg_count=avg_count,
                avg_stay_ms=int(round(_safe_average(stay_values_by_zone.get(zone.zone_id, [])))),
                total_entries=entries_by_zone.get(zone.zone_id, 0),
                total_exits=exits_by_zone.get(zone.zone_id, 0),
                queue_peak_count=max(queue_by_time.values()) if queue_by_time else 0,
                # 区域利用率按 0~1 展示，超容量信息由 peak_count 单独反映。
                utilization_rate=_safe_utilization_rate(peak_count, zone.capacity),
                by_crowd_type=zone_type_stats,
            )
        )

    return zone_stats


def _build_exit_stats(
    *,
    exits: list[ExperimentExit],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
    time_range: TimeRangeData,
) -> list[ExitStatsItem]:
    transitions_by_exit: dict[str, int] = defaultdict(int)
    queue_counts_by_exit_time: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    wait_values_by_exit: dict[str, list[int]] = defaultdict(list)
    exit_by_pair = {(topology_exit.source_zone_id, topology_exit.target_zone_id): topology_exit.exit_id for topology_exit in exits}

    for point in points:
        if point.target_exit_id is not None and point.is_queuing:
            queue_counts_by_exit_time[point.target_exit_id][int(point.simulation_time_ms)] += 1

    for decision in decisions:
        if decision.chosen_exit_id is not None:
            wait_values_by_exit[decision.chosen_exit_id].append(int(decision.stay_duration_ms))

    for agent_points in _group_points_by_agent(points).values():
        # 优先使用目标出口，其次再回退到“区域迁移对”映射出口流量。
        for previous, current in zip(agent_points, agent_points[1:]):
            if previous.zone_id == current.zone_id:
                continue
            exit_id = previous.target_exit_id
            if exit_id is None:
                exit_id = exit_by_pair.get((previous.zone_id, current.zone_id))
            if exit_id is not None:
                transitions_by_exit[exit_id] += 1

    duration_minutes = max((time_range.end_ms - time_range.start_ms) / 60000.0, 0.0)

    return [
        ExitStatsItem(
            exit_id=topology_exit.exit_id,
            total_passages=transitions_by_exit.get(topology_exit.exit_id, 0),
            avg_queue_length=_safe_average(list(queue_counts_by_exit_time.get(topology_exit.exit_id, {}).values())),
            max_queue_length=max(queue_counts_by_exit_time.get(topology_exit.exit_id, {}).values(), default=0),
            avg_wait_ms=int(round(_safe_average(wait_values_by_exit.get(topology_exit.exit_id, [])))),
            max_wait_ms=max(wait_values_by_exit.get(topology_exit.exit_id, []), default=0),
            throughput_per_min=(
                transitions_by_exit.get(topology_exit.exit_id, 0) / duration_minutes if duration_minutes > 0 else 0.0
            ),
        )
        for topology_exit in exits
    ]


def _build_decision_stats(decisions: list[AgentDecision]) -> DecisionStatsSummary:
    total_agents_decided = len(decisions)
    llm_success_count = sum(1 for decision in decisions if decision.decision_source == DecisionSource.LLM)
    llm_fallback_count = sum(1 for decision in decisions if decision.decision_source == DecisionSource.FALLBACK)
    algorithm_override_count = sum(1 for decision in decisions if bool(decision.was_overridden))
    latency_values = [int(decision.llm_latency_ms) for decision in decisions if decision.llm_latency_ms is not None]
    decisions_by_type: dict[str, list[AgentDecision]] = defaultdict(list)
    for decision in decisions:
        decisions_by_type[decision.crowd_type].append(decision)

    return DecisionStatsSummary(
        total_batches=len({decision.decision_batch_id for decision in decisions}),
        total_agents_decided=total_agents_decided,
        llm_success_count=llm_success_count,
        llm_fallback_count=llm_fallback_count,
        llm_fallback_rate=_safe_ratio(llm_fallback_count, llm_success_count + llm_fallback_count),
        avg_llm_latency_ms=int(round(_safe_average(latency_values))),
        algorithm_override_count=algorithm_override_count,
        algorithm_override_rate=_safe_ratio(algorithm_override_count, total_agents_decided),
        override_by_crowd_type={
            crowd_type: _safe_ratio(
                sum(1 for item in crowd_type_decisions if bool(item.was_overridden)),
                len(crowd_type_decisions),
            )
            for crowd_type, crowd_type_decisions in decisions_by_type.items()
        },
    )


def _build_zone_stats_from_aggregates(
    *,
    zones: list[ExperimentZone],
    rows: list[ZoneMetric1m],
    type_rows: list[ZoneMetric1m],
    crowd_type_filter: str | None,
    zone_id_filter: str | None,
) -> list[ZoneStatsItem]:
    target_zones = [zone for zone in zones if zone_id_filter is None or zone.zone_id == zone_id_filter]
    rows_by_zone: dict[str, list[ZoneMetric1m]] = defaultdict(list)
    type_rows_by_zone: dict[str, dict[str, list[ZoneMetric1m]]] = defaultdict(lambda: defaultdict(list))

    for row in rows:
        rows_by_zone[row.zone_id].append(row)
    for row in type_rows:
        if row.crowd_type is None:
            continue
        type_rows_by_zone[row.zone_id][row.crowd_type].append(row)

    zone_stats: list[ZoneStatsItem] = []
    for zone in target_zones:
        zone_rows = rows_by_zone.get(zone.zone_id, [])
        peak_row = min(zone_rows, key=lambda item: (-item.peak_count, item.bucket_start_ms)) if zone_rows else None
        peak_count = peak_row.peak_count if peak_row is not None else 0
        peak_time_ms = int(peak_row.bucket_start_ms) if peak_row is not None else 0
        avg_count = _weighted_average_metric(zone_rows, "avg_count")
        avg_stay_ms = _average_nullable_int(zone_rows, "avg_stay_ms")
        total_entries = sum(int(row.total_entries) for row in zone_rows)
        total_exits = sum(int(row.total_exits) for row in zone_rows)
        queue_peak_count = max((int(row.queue_peak_count) for row in zone_rows), default=0)

        zone_stats.append(
            ZoneStatsItem(
                zone_id=zone.zone_id,
                zone_name=zone.zone_name,
                peak_count=peak_count,
                peak_time_ms=peak_time_ms,
                avg_count=avg_count,
                avg_stay_ms=avg_stay_ms,
                total_entries=total_entries,
                total_exits=total_exits,
                queue_peak_count=queue_peak_count,
                # 聚合查询同样限制在 0~1，避免超容量实验导致响应校验失败。
                utilization_rate=_safe_utilization_rate(peak_count, zone.capacity),
                by_crowd_type={
                    crowd_type: ZoneCrowdTypeDistribution(
                        avg_count=_weighted_average_metric(crowd_rows, "avg_count"),
                        peak_count=max((int(item.peak_count) for item in crowd_rows), default=0),
                    )
                    for crowd_type, crowd_rows in type_rows_by_zone.get(zone.zone_id, {}).items()
                    if crowd_type_filter is None or crowd_type == crowd_type_filter
                },
            )
        )

    return zone_stats


def _build_exit_stats_from_aggregates(
    *,
    exits: list[ExperimentExit],
    rows: list[ExitMetric1m],
    time_range: TimeRangeData,
) -> list[ExitStatsItem]:
    rows_by_exit: dict[str, list[ExitMetric1m]] = defaultdict(list)
    for row in rows:
        rows_by_exit[row.exit_id].append(row)

    duration_minutes = max((time_range.end_ms - time_range.start_ms) / 60000.0, 0.0)

    return [
        ExitStatsItem(
            exit_id=topology_exit.exit_id,
            total_passages=sum(int(row.total_passages) for row in rows_by_exit.get(topology_exit.exit_id, [])),
            avg_queue_length=_simple_average_metric(rows_by_exit.get(topology_exit.exit_id, []), "avg_queue_length"),
            max_queue_length=max(
                (int(row.max_queue_length or 0) for row in rows_by_exit.get(topology_exit.exit_id, [])),
                default=0,
            ),
            avg_wait_ms=_average_nullable_int(rows_by_exit.get(topology_exit.exit_id, []), "avg_wait_ms"),
            max_wait_ms=max(
                (int(row.max_wait_ms or 0) for row in rows_by_exit.get(topology_exit.exit_id, [])),
                default=0,
            ),
            throughput_per_min=(
                sum(int(row.total_passages) for row in rows_by_exit.get(topology_exit.exit_id, [])) / duration_minutes
                if duration_minutes > 0
                else 0.0
            ),
        )
        for topology_exit in exits
    ]


def _build_decision_stats_from_aggregates(
    *,
    rows: list[DecisionMetric1m],
    type_rows: list[DecisionMetric1m],
    total_batches: int,
) -> DecisionStatsSummary:
    total_agents_decided = sum(int(row.decided_count) for row in rows)
    llm_success_count = sum(int(row.llm_success_count) for row in rows)
    llm_fallback_count = sum(int(row.fallback_count) for row in rows)
    algorithm_override_count = sum(int(row.override_count) for row in rows)
    latency_weights = [
        (int(row.avg_llm_latency_ms), int(row.decided_count))
        for row in rows
        if row.avg_llm_latency_ms is not None
    ]

    rows_by_crowd_type: dict[str, list[DecisionMetric1m]] = defaultdict(list)
    for row in type_rows:
        if row.crowd_type is not None:
            rows_by_crowd_type[row.crowd_type].append(row)

    return DecisionStatsSummary(
        total_batches=total_batches,
        total_agents_decided=total_agents_decided,
        llm_success_count=llm_success_count,
        llm_fallback_count=llm_fallback_count,
        llm_fallback_rate=_safe_ratio(llm_fallback_count, llm_success_count + llm_fallback_count),
        avg_llm_latency_ms=_weighted_average_latency(latency_weights),
        algorithm_override_count=algorithm_override_count,
        algorithm_override_rate=_safe_ratio(algorithm_override_count, total_agents_decided),
        override_by_crowd_type={
            crowd_type: _safe_ratio(
                sum(int(item.override_count) for item in crowd_rows),
                sum(int(item.decided_count) for item in crowd_rows),
            )
            for crowd_type, crowd_rows in rows_by_crowd_type.items()
        },
    )


def _list_zone_metric_rows(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    zone_id: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[ZoneMetric1m]:
    stmt = select(ZoneMetric1m).where(ZoneMetric1m.experiment_id == experiment_pk)
    if crowd_type == "__all_types__":
        stmt = stmt.where(ZoneMetric1m.crowd_type.is_not(None))
    elif crowd_type is None:
        stmt = stmt.where(ZoneMetric1m.crowd_type.is_(None))
    else:
        stmt = stmt.where(ZoneMetric1m.crowd_type == crowd_type)
    if zone_id is not None:
        stmt = stmt.where(ZoneMetric1m.zone_id == zone_id)
    if time_start_ms is not None:
        stmt = stmt.where(ZoneMetric1m.bucket_start_ms >= bucket_start_ms(time_start_ms))
    if time_end_ms is not None:
        stmt = stmt.where(ZoneMetric1m.bucket_start_ms <= bucket_start_ms(time_end_ms))
    stmt = stmt.order_by(ZoneMetric1m.bucket_start_ms, ZoneMetric1m.zone_id)
    return list(session.scalars(stmt))


def _list_exit_metric_rows(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[ExitMetric1m]:
    stmt = select(ExitMetric1m).where(ExitMetric1m.experiment_id == experiment_pk)
    if crowd_type is None:
        stmt = stmt.where(ExitMetric1m.crowd_type.is_(None))
    else:
        stmt = stmt.where(ExitMetric1m.crowd_type == crowd_type)
    if time_start_ms is not None:
        stmt = stmt.where(ExitMetric1m.bucket_start_ms >= bucket_start_ms(time_start_ms))
    if time_end_ms is not None:
        stmt = stmt.where(ExitMetric1m.bucket_start_ms <= bucket_start_ms(time_end_ms))
    stmt = stmt.order_by(ExitMetric1m.bucket_start_ms, ExitMetric1m.exit_id)
    return list(session.scalars(stmt))


def _list_decision_metric_rows(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[DecisionMetric1m]:
    stmt = select(DecisionMetric1m).where(DecisionMetric1m.experiment_id == experiment_pk)
    if crowd_type == "__all_types__":
        stmt = stmt.where(DecisionMetric1m.crowd_type.is_not(None))
    elif crowd_type is None:
        stmt = stmt.where(DecisionMetric1m.crowd_type.is_(None))
    else:
        stmt = stmt.where(DecisionMetric1m.crowd_type == crowd_type)
    if time_start_ms is not None:
        stmt = stmt.where(DecisionMetric1m.bucket_start_ms >= bucket_start_ms(time_start_ms))
    if time_end_ms is not None:
        stmt = stmt.where(DecisionMetric1m.bucket_start_ms <= bucket_start_ms(time_end_ms))
    stmt = stmt.order_by(DecisionMetric1m.bucket_start_ms)
    return list(session.scalars(stmt))


def _list_agent_metric_rows(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[AgentMetric1m]:
    stmt = select(AgentMetric1m).where(AgentMetric1m.experiment_id == experiment_pk)
    if crowd_type == "__all_types__":
        stmt = stmt.where(AgentMetric1m.crowd_type.is_not(None))
    elif crowd_type is None:
        stmt = stmt.where(AgentMetric1m.crowd_type.is_(None))
    else:
        stmt = stmt.where(AgentMetric1m.crowd_type == crowd_type)
    if time_start_ms is not None:
        stmt = stmt.where(AgentMetric1m.bucket_start_ms >= bucket_start_ms(time_start_ms))
    if time_end_ms is not None:
        stmt = stmt.where(AgentMetric1m.bucket_start_ms <= bucket_start_ms(time_end_ms))
    stmt = stmt.order_by(AgentMetric1m.bucket_start_ms)
    return list(session.scalars(stmt))


def _weighted_average_metric(rows: list[object], field_name: str) -> float:
    weighted_sum = 0.0
    total_weight = 0
    for row in rows:
        weight = max(int(getattr(row, "sample_count", 0) or 0), 1)
        value = decimal_to_float(getattr(row, field_name)) or 0.0
        weighted_sum += value * weight
        total_weight += weight
    if total_weight <= 0:
        return 0.0
    return round(weighted_sum / total_weight, 3)


def _simple_average_metric(rows: list[object], field_name: str) -> float:
    values = [decimal_to_float(getattr(row, field_name)) or 0.0 for row in rows]
    return _safe_average(values)


def _average_nullable_int(rows: list[object], field_name: str) -> int:
    values = [int(value) for row in rows if (value := getattr(row, field_name)) is not None]
    if not values:
        return 0
    return int(round(sum(values) / len(values)))


def _weighted_average_latency(values_with_weights: list[tuple[int, int]]) -> int:
    if not values_with_weights:
        return 0
    weighted_sum = sum(value * max(weight, 1) for value, weight in values_with_weights)
    total_weight = sum(max(weight, 1) for _, weight in values_with_weights)
    if total_weight <= 0:
        return 0
    return int(round(weighted_sum / total_weight))


def _build_trajectory_record(point: TrajectoryPoint) -> TrajectoryRecordItem:
    return TrajectoryRecordItem(
        agent_id=point.agent_id,
        crowd_type=point.crowd_type,
        timestamp_ms=int(point.simulation_time_ms),
        position=Position2D(
            x=decimal_to_float(point.position_x) or 0.0,
            z=decimal_to_float(point.position_z) or 0.0,
        ),
        zone_id=point.zone_id,
        speed=decimal_to_float(point.speed) or 0.0,
        state=point.state,
        target_exit_id=point.target_exit_id,
        is_queuing=bool(point.is_queuing),
    )


def _build_compact_point(point: TrajectoryPoint) -> tuple[int, float, float, str, float, object, bool]:
    return (
        int(point.simulation_time_ms),
        round_coordinate(decimal_to_float(point.position_x) or 0.0),
        round_coordinate(decimal_to_float(point.position_z) or 0.0),
        point.zone_id,
        decimal_to_float(point.speed) or 0.0,
        point.state,
        bool(point.is_queuing),
    )


def _build_agent_decision_detail(decision: AgentDecision, batch_id: str) -> AgentDecisionDetail:
    return AgentDecisionDetail(
        batch_id=batch_id,
        simulation_time_ms=int(decision.simulation_time_ms),
        current_zone_id=decision.current_zone_id,
        stay_duration_ms=int(decision.stay_duration_ms),
        patience_ratio=decimal_to_float(decision.patience_ratio) or 0.0,
        chosen_exit_id=decision.chosen_exit_id,
        decision_source=decision.decision_source,
        reasoning=decision.reasoning,
        confidence=decimal_to_float(decision.confidence),
        algorithm_recommendation=decision.algorithm_recommendation,
        was_overridden=bool(decision.was_overridden),
    )


def _validate_raw_trajectory_query(params: object) -> None:
    settings = get_settings()
    if int(getattr(params, "page_size", 0) or 0) > settings.trajectory_query_max_raw_page_size:
        raise AppError(
            message=(
                f"原始轨迹明细查询 page_size 不能超过 {settings.trajectory_query_max_raw_page_size}，"
                "请缩小分页大小或改用 compact 格式"
            ),
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=400,
        )

    time_start_ms = getattr(params, "time_start_ms", None)
    time_end_ms = getattr(params, "time_end_ms", None)
    if time_start_ms is None or time_end_ms is None:
        return

    if int(time_end_ms) - int(time_start_ms) > settings.trajectory_query_max_raw_window_ms:
        raise AppError(
            message=(
                f"原始轨迹明细查询时间窗口不能超过 {settings.trajectory_query_max_raw_window_ms} 毫秒，"
                "请缩小时间范围或改用 compact 格式"
            ),
            code=error_codes.INVALID_FIELD_VALUE,
            status_code=400,
        )


def _build_sampled_trajectory_filters(
    *,
    experiment_pk: int,
    agent_id: str | None,
    crowd_type: str | None,
    zone_id: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
    state: object | None,
    is_queuing: bool | None,
) -> list[object]:
    filters: list[object] = [TrajectoryPointSampled1s.experiment_id == experiment_pk]
    if agent_id is not None:
        filters.append(TrajectoryPointSampled1s.agent_id == agent_id)
    if crowd_type is not None:
        filters.append(TrajectoryPointSampled1s.crowd_type == crowd_type)
    if zone_id is not None:
        filters.append(TrajectoryPointSampled1s.zone_id == zone_id)
    if time_start_ms is not None:
        filters.append(TrajectoryPointSampled1s.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        filters.append(TrajectoryPointSampled1s.simulation_time_ms <= time_end_ms)
    if state is not None:
        filters.append(TrajectoryPointSampled1s.state == state)
    if is_queuing is not None:
        filters.append(TrajectoryPointSampled1s.is_queuing == is_queuing)
    return filters


def _build_trajectory_filters(
    *,
    experiment_pk: int,
    agent_id: str | None,
    crowd_type: str | None,
    zone_id: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
    state: object | None,
    is_queuing: bool | None,
) -> list[object]:
    filters: list[object] = [TrajectoryPoint.experiment_id == experiment_pk]
    if agent_id is not None:
        filters.append(TrajectoryPoint.agent_id == agent_id)
    if crowd_type is not None:
        filters.append(TrajectoryPoint.crowd_type == crowd_type)
    if zone_id is not None:
        filters.append(TrajectoryPoint.zone_id == zone_id)
    if time_start_ms is not None:
        filters.append(TrajectoryPoint.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        filters.append(TrajectoryPoint.simulation_time_ms <= time_end_ms)
    if state is not None:
        filters.append(TrajectoryPoint.state == state)
    if is_queuing is not None:
        filters.append(TrajectoryPoint.is_queuing == is_queuing)
    return filters


def _count_trajectory_points(session: Session, filters: list[object]) -> int:
    stmt: Select[tuple[int]] = select(func.count()).select_from(TrajectoryPoint).where(*filters)
    return int(session.scalar(stmt) or 0)


def _list_trajectory_points_page(
    session: Session,
    filters: list[object],
    *,
    offset: int,
    limit: int,
) -> list[TrajectoryPoint]:
    stmt = (
        select(TrajectoryPoint)
        .where(*filters)
        .order_by(TrajectoryPoint.simulation_time_ms, TrajectoryPoint.agent_id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _list_all_trajectory_points(session: Session, filters: list[object]) -> list[TrajectoryPoint]:
    stmt = select(TrajectoryPoint).where(*filters).order_by(TrajectoryPoint.simulation_time_ms, TrajectoryPoint.agent_id)
    return list(session.scalars(stmt))


def _count_sampled_trajectory_points(session: Session, filters: list[object]) -> int:
    stmt: Select[tuple[int]] = select(func.count()).select_from(TrajectoryPointSampled1s).where(*filters)
    return int(session.scalar(stmt) or 0)


def _list_sampled_trajectory_points_page(
    session: Session,
    filters: list[object],
    *,
    offset: int,
    limit: int,
) -> list[TrajectoryPointSampled1s]:
    stmt = (
        select(TrajectoryPointSampled1s)
        .where(*filters)
        .order_by(TrajectoryPointSampled1s.simulation_time_ms, TrajectoryPointSampled1s.agent_id)
        .offset(offset)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def _list_sampled_trajectory_points_for_agents(
    session: Session,
    *,
    experiment_pk: int,
    agent_ids: list[str],
    crowd_type: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[TrajectoryPointSampled1s]:
    if not agent_ids:
        return []

    filters: list[object] = [
        TrajectoryPointSampled1s.experiment_id == experiment_pk,
        TrajectoryPointSampled1s.agent_id.in_(agent_ids),
    ]
    if crowd_type is not None:
        filters.append(TrajectoryPointSampled1s.crowd_type == crowd_type)
    if time_start_ms is not None:
        filters.append(TrajectoryPointSampled1s.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        filters.append(TrajectoryPointSampled1s.simulation_time_ms <= time_end_ms)

    stmt = (
        select(TrajectoryPointSampled1s)
        .where(*filters)
        .order_by(TrajectoryPointSampled1s.agent_id, TrajectoryPointSampled1s.simulation_time_ms)
    )
    return list(session.scalars(stmt))


def _list_trajectory_points_for_agents(
    session: Session,
    *,
    experiment_pk: int,
    agent_ids: list[str],
    crowd_type: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[TrajectoryPoint]:
    if not agent_ids:
        return []

    filters: list[object] = [
        TrajectoryPoint.experiment_id == experiment_pk,
        TrajectoryPoint.agent_id.in_(agent_ids),
    ]
    if crowd_type is not None:
        filters.append(TrajectoryPoint.crowd_type == crowd_type)
    if time_start_ms is not None:
        filters.append(TrajectoryPoint.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        filters.append(TrajectoryPoint.simulation_time_ms <= time_end_ms)

    stmt = (
        select(TrajectoryPoint)
        .where(*filters)
        .order_by(TrajectoryPoint.agent_id, TrajectoryPoint.simulation_time_ms)
    )
    return list(session.scalars(stmt))


def _list_filtered_trajectory_points(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    zone_id: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[TrajectoryPoint]:
    return _list_all_trajectory_points(
        session,
        _build_trajectory_filters(
            experiment_pk=experiment_pk,
            agent_id=None,
            crowd_type=crowd_type,
            zone_id=zone_id,
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
            state=None,
            is_queuing=None,
        ),
    )


def _list_filtered_decisions(
    session: Session,
    *,
    experiment_pk: int,
    crowd_type: str | None,
    zone_id: str | None,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> list[AgentDecision]:
    stmt = select(AgentDecision).where(AgentDecision.experiment_id == experiment_pk)
    if crowd_type is not None:
        stmt = stmt.where(AgentDecision.crowd_type == crowd_type)
    if zone_id is not None:
        stmt = stmt.where(AgentDecision.current_zone_id == zone_id)
    if time_start_ms is not None:
        stmt = stmt.where(AgentDecision.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        stmt = stmt.where(AgentDecision.simulation_time_ms <= time_end_ms)
    stmt = stmt.order_by(AgentDecision.simulation_time_ms, AgentDecision.id)
    return list(session.scalars(stmt))


def _count_decision_batches_in_range(
    session: Session,
    *,
    experiment_pk: int,
    time_start_ms: int | None,
    time_end_ms: int | None,
) -> int:
    stmt = select(func.count(func.distinct(DecisionBatch.id))).where(DecisionBatch.experiment_id == experiment_pk)
    if time_start_ms is not None:
        stmt = stmt.where(DecisionBatch.simulation_time_ms >= time_start_ms)
    if time_end_ms is not None:
        stmt = stmt.where(DecisionBatch.simulation_time_ms <= time_end_ms)
    return int(session.scalar(stmt) or 0)


def _build_heatmap_cells(
    *,
    points: list[TrajectoryPoint],
    grid_config: GridConfig,
    metric: HeatmapMetric,
    normalization: NormalizationMode,
) -> tuple[list[float], HeatmapStatistics]:
    """把轨迹点投影到热力图网格，并按指标计算原始单元格值。

    raw_values 保留真实业务量，例如停留毫秒、通过次数或速度均值；normalization 只影响
    前端展示色阶，不改变 statistics 中的 raw_* 字段。
    """

    total_cells = grid_config.cols * grid_config.rows
    raw_values = [0.0 for _ in range(total_cells)]
    avg_speed_sum = [0.0 for _ in range(total_cells)]
    avg_speed_count = [0 for _ in range(total_cells)]
    peak_counts: list[dict[int, int]] = [defaultdict(int) for _ in range(total_cells)]

    # 先把轨迹点映射进网格并累计原始指标，再统一做归一化。
    for point in points:
        cell_index = _resolve_cell_index(grid_config, point)
        if cell_index is None:
            continue

        if metric == HeatmapMetric.STAY_DURATION:
            raw_values[cell_index] += float(point.sample_interval_ms)
            continue

        if metric == HeatmapMetric.PASSAGE_COUNT:
            raw_values[cell_index] += 1.0
            continue

        if metric == HeatmapMetric.PEAK_DENSITY:
            peak_counts[cell_index][int(point.simulation_time_ms)] += 1
            continue

        if metric == HeatmapMetric.QUEUE_FREQUENCY:
            if point.is_queuing:
                raw_values[cell_index] += 1.0
            continue

        if metric == HeatmapMetric.AVG_SPEED:
            avg_speed_sum[cell_index] += decimal_to_float(point.speed) or 0.0
            avg_speed_count[cell_index] += 1

    if metric == HeatmapMetric.PEAK_DENSITY:
        raw_values = [
            float(max(counts.values()) if counts else 0.0)
            for counts in peak_counts
        ]
    elif metric == HeatmapMetric.AVG_SPEED:
        raw_values = [
            round(avg_speed_sum[index] / avg_speed_count[index], 6) if avg_speed_count[index] else 0.0
            for index in range(total_cells)
        ]

    cells, statistics = _normalize_heatmap_values(raw_values, normalization)
    statistics.total_cells = total_cells
    return cells, statistics


def _get_or_create_heatmap(
    session: Session,
    *,
    experiment_pk: int,
    external_experiment_id: str,
    runtime_data_cleaned: bool,
    time_start_ms: int,
    time_end_ms: int,
    grid_config: GridConfig,
    metric: HeatmapMetric,
    crowd_type: str | None,
    zone_id: str | None,
    normalization: NormalizationMode,
) -> tuple[HeatmapData, bool]:
    """读取热力图缓存，未命中时回源计算并写回。

    缓存顺序是 Redis -> heatmap_cache 表 -> 原始 trajectory_points。Redis 负责热数据，
    数据库缓存负责跨进程和清理后的持久访问；如果原始运行数据已清理且没有缓存，
    这里会返回 409，提醒调用方只能访问已预热过的热力图。
    """

    settings = get_settings()
    cache_ttl_seconds = max(settings.heatmap_cache_ttl_seconds, 0)
    redis_client = get_redis_client()
    grid_hash = _build_heatmap_grid_hash(grid_config)
    cache_key = _build_heatmap_cache_key(
        experiment_pk=experiment_pk,
        time_start_ms=time_start_ms,
        time_end_ms=time_end_ms,
        grid_hash=grid_hash,
        metric=metric,
        crowd_type=crowd_type,
        zone_id=zone_id,
        normalization=normalization,
    )
    redis_cache_parts = _build_heatmap_redis_cache_parts(experiment_pk=experiment_pk, cache_key=cache_key)
    redis_payload = redis_client.get_json(*redis_cache_parts)
    if isinstance(redis_payload, dict):
        logger.info(
            "热力图 Redis 热点缓存命中: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s",
            external_experiment_id,
            cache_key,
            metric,
            crowd_type,
            zone_id,
        )
        return HeatmapData.model_validate(redis_payload), True

    cache_repository = HeatmapCacheRepository(session)
    cached_row = cache_repository.get_by_cache_key(cache_key)

    if cached_row is not None and _is_heatmap_cache_valid(cached_row):
        logger.info(
            "热力图缓存命中: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s expires_at=%s",
            external_experiment_id,
            cache_key,
            metric,
            crowd_type,
            zone_id,
            cached_row.expires_at,
        )
        data = _build_heatmap_data_from_cache(cached_row, grid_config)
        _write_heatmap_to_redis(redis_client, redis_cache_parts, data, cache_ttl_seconds)
        return data, True

    if cached_row is None:
        logger.info(
            "热力图缓存未命中: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s",
            external_experiment_id,
            cache_key,
            metric,
            crowd_type,
            zone_id,
        )
    else:
        logger.info(
            "热力图缓存已过期，准备回源重算: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s expired_at=%s",
            external_experiment_id,
            cache_key,
            metric,
            crowd_type,
            zone_id,
            cached_row.expires_at,
        )

    if runtime_data_cleaned:
        logger.warning(
            "热力图缓存未命中且原始运行数据已清理，无法回源重算: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s",
            external_experiment_id,
            cache_key,
            metric,
            crowd_type,
            zone_id,
        )
        raise AppError(
            message=f"实验 `{external_experiment_id}` 的原始运行数据已清理，仅能访问已预热的热力图缓存",
            code=error_codes.EXPERIMENT_STATUS_CONFLICT,
            status_code=409,
        )

    points = _list_filtered_trajectory_points(
        session,
        experiment_pk=experiment_pk,
        crowd_type=crowd_type,
        zone_id=zone_id,
        time_start_ms=time_start_ms,
        time_end_ms=time_end_ms,
    )
    cells, statistics = _build_heatmap_cells(
        points=points,
        grid_config=grid_config,
        metric=metric,
        normalization=normalization,
    )
    data = HeatmapData(
        grid_config=grid_config,
        metric=metric,
        normalization=normalization,
        crowd_type_filter=crowd_type,
        statistics=statistics,
        cells=cells,
    )

    if cache_ttl_seconds <= 0:
        logger.info(
            "热力图缓存已禁用，直接返回回源结果: experiment_id=%s metric=%s crowd_type=%s zone_id=%s point_count=%s",
            external_experiment_id,
            metric,
            crowd_type,
            zone_id,
            len(points),
        )
        return data, False

    generated_at = datetime.now(timezone.utc)
    expires_at = generated_at + timedelta(seconds=cache_ttl_seconds)
    cache_repository.save(
        HeatmapCache(
            cache_key=cache_key,
            experiment_id=experiment_pk,
            metric=metric,
            time_start_ms=time_start_ms,
            time_end_ms=time_end_ms,
            crowd_type_filter=crowd_type,
            zone_id_filter=zone_id,
            normalization=normalization,
            grid_hash=grid_hash,
            statistics_json=statistics.model_dump(mode="json"),
            cells=cells,
            expires_at=expires_at,
            created_at=generated_at,
        )
    )
    session.commit()
    _write_heatmap_to_redis(redis_client, redis_cache_parts, data, cache_ttl_seconds)
    logger.info(
        "热力图缓存写入完成: experiment_id=%s cache_key=%s metric=%s crowd_type=%s zone_id=%s point_count=%s expires_at=%s",
        external_experiment_id,
        cache_key,
        metric,
        crowd_type,
        zone_id,
        len(points),
        expires_at,
    )
    return data, False


def _build_heatmap_grid_hash(grid_config: GridConfig) -> str:
    """为网格配置生成稳定哈希，确保同样网格能命中同一份缓存。"""

    payload = json.dumps(grid_config.model_dump(mode="json"), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _build_heatmap_redis_cache_parts(*, experiment_pk: int, cache_key: str) -> tuple[str, ...]:
    return ("heatmap", "response", "exp", str(experiment_pk), cache_key)


def _write_heatmap_to_redis(
    redis_client: object,
    cache_parts: tuple[str, ...],
    data: HeatmapData,
    ttl_seconds: int,
) -> None:
    if ttl_seconds <= 0 or not hasattr(redis_client, "set_json"):
        return
    redis_client.set_json(*cache_parts, value=data.model_dump(mode="json"), ttl_seconds=ttl_seconds)


def _was_runtime_data_cleaned(experiment: object) -> bool:
    summary_json = getattr(experiment, "summary_json", None) or {}
    runtime_cleanup = summary_json.get("runtime_data_cleanup")
    return isinstance(runtime_cleanup, dict)


def _get_heatmap_time_range(session: Session, *, experiment_pk: int) -> tuple[int, int] | None:
    stmt = select(
        func.min(TrajectoryPoint.simulation_time_ms),
        func.max(TrajectoryPoint.simulation_time_ms),
    ).where(TrajectoryPoint.experiment_id == experiment_pk)
    start_ms, end_ms = session.execute(stmt).one()
    if start_ms is None or end_ms is None:
        return None
    return int(start_ms), int(end_ms)


def _build_default_heatmap_grid_config(
    *,
    zones: list[ExperimentZone],
    cell_size: float,
) -> GridConfig:
    """根据实验区域边界生成默认热力图网格，用于维护任务预热。"""

    if not zones:
        return GridConfig(
            origin=GridOrigin(x=0.0, z=0.0),
            cell_size=cell_size,
            cols=1,
            rows=1,
        )

    min_x = min(decimal_to_float(zone.min_x) or 0.0 for zone in zones)
    min_z = min(decimal_to_float(zone.min_z) or 0.0 for zone in zones)
    max_x = max(decimal_to_float(zone.max_x) or 0.0 for zone in zones)
    max_z = max(decimal_to_float(zone.max_z) or 0.0 for zone in zones)
    width = max(max_x - min_x, cell_size)
    height = max(max_z - min_z, cell_size)

    return GridConfig(
        origin=GridOrigin(x=round_coordinate(min_x), z=round_coordinate(min_z)),
        cell_size=cell_size,
        cols=max(1, min(200, ceil(width / cell_size))),
        rows=max(1, min(200, ceil(height / cell_size))),
    )


def _build_heatmap_cache_key(
    *,
    experiment_pk: int,
    time_start_ms: int,
    time_end_ms: int,
    grid_hash: str,
    metric: HeatmapMetric,
    crowd_type: str | None,
    zone_id: str | None,
    normalization: NormalizationMode,
) -> str:
    """把实验、时间窗、网格、指标和过滤条件合成热力图缓存键。"""

    payload = {
        "version": 1,
        "experiment_pk": experiment_pk,
        "time_start_ms": time_start_ms,
        "time_end_ms": time_end_ms,
        "grid_hash": grid_hash,
        "metric": metric.value,
        "crowd_type": crowd_type,
        "zone_id": zone_id,
        "normalization": normalization.value,
    }
    serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _is_heatmap_cache_valid(row: HeatmapCache) -> bool:
    """判断数据库热力图缓存是否仍在有效期内。"""

    if row.expires_at is None:
        return True
    return row.expires_at > datetime.now(timezone.utc)


def _build_heatmap_data_from_cache(row: HeatmapCache, grid_config: GridConfig) -> HeatmapData:
    """把数据库缓存行还原成 API 层热力图响应对象。"""

    return HeatmapData(
        grid_config=grid_config,
        metric=row.metric,
        normalization=row.normalization,
        crowd_type_filter=row.crowd_type_filter,
        statistics=HeatmapStatistics.model_validate(row.statistics_json),
        cells=[float(cell) for cell in row.cells],
    )


def _resolve_cell_index(grid_config: GridConfig, point: TrajectoryPoint) -> int | None:
    """计算轨迹点所在的网格单元；落在网格外的点返回 None。"""

    x = decimal_to_float(point.position_x) or 0.0
    z = decimal_to_float(point.position_z) or 0.0
    offset_x = x - grid_config.origin.x
    offset_z = z - grid_config.origin.z
    if offset_x < 0 or offset_z < 0:
        return None

    col = int(offset_x / grid_config.cell_size)
    row = int(offset_z / grid_config.cell_size)
    if col < 0 or col >= grid_config.cols or row < 0 or row >= grid_config.rows:
        return None

    return row * grid_config.cols + col


def _normalize_heatmap_values(
    raw_values: list[float],
    normalization: NormalizationMode,
) -> tuple[list[float], HeatmapStatistics]:
    """按指定模式归一化热力图展示值，并保留原始统计摘要。"""

    raw_min = min(raw_values, default=0.0)
    raw_max = max(raw_values, default=0.0)
    percentile_99_raw = _calculate_percentile(raw_values, 0.99)
    non_empty_cells = sum(1 for value in raw_values if value > 0)

    # 不同归一化模式只影响展示值，不改变 raw_* 统计口径。
    if normalization == NormalizationMode.NONE:
        normalized = [round(value, 6) for value in raw_values]
    elif normalization == NormalizationMode.MIN_MAX:
        if raw_max <= raw_min:
            normalized = [1.0 if value > 0 else 0.0 for value in raw_values]
        else:
            normalized = [round((value - raw_min) / (raw_max - raw_min), 6) for value in raw_values]
    elif normalization == NormalizationMode.PERCENTILE_99:
        scale = percentile_99_raw if percentile_99_raw > 0 else raw_max
        if scale <= 0:
            normalized = [0.0 for _ in raw_values]
        else:
            normalized = [round(min(value / scale, 1.0), 6) for value in raw_values]
    else:
        if raw_max <= 0:
            normalized = [0.0 for _ in raw_values]
        else:
            denominator = log1p(raw_max)
            normalized = [round(log1p(value) / denominator, 6) if value > 0 else 0.0 for value in raw_values]

    return normalized, HeatmapStatistics(
        min_value=min(normalized, default=0.0),
        max_value=max(normalized, default=0.0),
        raw_min=round(raw_min, 6),
        raw_max=round(raw_max, 6),
        percentile_99_raw=round(percentile_99_raw, 6) if percentile_99_raw > 0 else None,
        non_empty_cells=non_empty_cells,
        total_cells=len(raw_values),
    )


def _calculate_percentile(values: list[float], percentile: float) -> float:
    positive_values = sorted(value for value in values if value > 0)
    if not positive_values:
        return 0.0
    if len(positive_values) == 1:
        return positive_values[0]

    position = (len(positive_values) - 1) * percentile
    lower = int(position)
    upper = min(lower + 1, len(positive_values) - 1)
    weight = position - lower
    return positive_values[lower] * (1 - weight) + positive_values[upper] * weight


def _group_points_by_agent(points: list[TrajectoryPoint]) -> dict[str, list[TrajectoryPoint]]:
    grouped: dict[str, list[TrajectoryPoint]] = defaultdict(list)
    for point in points:
        grouped[point.agent_id].append(point)
    return grouped


def _safe_average(values: list[float | int]) -> float:
    if not values:
        return 0.0
    return round(sum(float(value) for value in values) / len(values), 3)


def _safe_ratio(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round(float(numerator) / float(denominator), 4)


def _safe_utilization_rate(numerator: int | float, denominator: int | float | None) -> float:
    """区域利用率对外只展示到 100%，超载部分由峰值人数体现。"""

    ratio = _safe_ratio(numerator, denominator or 0)
    return min(ratio, 1.0)


def _sample_path_trace_points(points: list[TrajectoryPoint], sample_rate: int) -> list[tuple[int, float, float]]:
    sampled = [
        (
            int(point.simulation_time_ms),
            round_coordinate(decimal_to_float(point.position_x) or 0.0),
            round_coordinate(decimal_to_float(point.position_z) or 0.0),
        )
        for point in points[::sample_rate]
    ]
    if points:
        last_tuple = (
            int(points[-1].simulation_time_ms),
            round_coordinate(decimal_to_float(points[-1].position_x) or 0.0),
            round_coordinate(decimal_to_float(points[-1].position_z) or 0.0),
        )
        # 即使抽样率较大，也强制保留最后一个点，避免路径尾部被截断。
        if not sampled or sampled[-1] != last_tuple:
            sampled.append(last_tuple)
    return sampled
