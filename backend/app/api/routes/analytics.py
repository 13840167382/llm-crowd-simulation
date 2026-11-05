"""分析查询相关路由。"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from backend.app.api.deps import experiment_id_header, request_id_header
from backend.app.api.response import ApiResponse, success_response
from backend.app.application.analytics.service import (
    compare_heatmap as compare_heatmap_service,
    create_heatmap as create_heatmap_service,
    get_agent_trajectory_detail as get_agent_trajectory_detail_service,
    get_experiment_stats as get_experiment_stats_service,
    get_path_traces as get_path_traces_service,
    get_trajectories as get_trajectories_service,
)
from backend.app.infrastructure.db.session import get_db_session
from backend.app.schemas.analytics import (
    AgentTrajectoryDetailData,
    AgentTrajectoryQueryParams,
    ExperimentStatsData,
    HeatmapCompareData,
    HeatmapCompareRequest,
    HeatmapData,
    HeatmapRequest,
    PathTracesData,
    PathTracesQueryParams,
    StatsQueryParams,
    TrajectoriesCompactData,
    TrajectoriesQueryParams,
    TrajectoriesRecordsData,
)

router = APIRouter(prefix="/experiments", tags=["分析"])


@router.get(
    "/{experiment_id}/stats",
    summary="获取实验统计概览",
    response_model=ApiResponse[ExperimentStatsData],
)
def get_experiment_stats(
    experiment_id: str,
    params: StatsQueryParams = Depends(),
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_experiment_stats_service(session, experiment_id=experiment_id, params=params)
    return success_response(data=data)


@router.get(
    "/{experiment_id}/trajectories",
    summary="查询轨迹数据",
    response_model=ApiResponse[TrajectoriesRecordsData | TrajectoriesCompactData],
)
def get_trajectories(
    experiment_id: str,
    params: TrajectoriesQueryParams = Depends(),
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_trajectories_service(session, experiment_id=experiment_id, params=params)
    return success_response(data=data)


@router.get(
    "/{experiment_id}/trajectories/{agent_id}",
    summary="查询单个体轨迹详情",
    response_model=ApiResponse[AgentTrajectoryDetailData],
)
def get_agent_trajectory(
    experiment_id: str,
    agent_id: str,
    params: AgentTrajectoryQueryParams = Depends(),
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_agent_trajectory_detail_service(
        session,
        experiment_id=experiment_id,
        agent_id=agent_id,
        params=params,
    )
    return success_response(data=data)


@router.post(
    "/{experiment_id}/heatmap",
    summary="生成热力图数据",
    response_model=ApiResponse[HeatmapData],
)
def create_heatmap(
    experiment_id: str,
    payload: HeatmapRequest,
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = create_heatmap_service(session, experiment_id=experiment_id, payload=payload)
    return success_response(data=data)


@router.post(
    "/{experiment_id}/heatmap/compare",
    summary="生成对比热力图数据",
    response_model=ApiResponse[HeatmapCompareData],
)
def compare_heatmap(
    experiment_id: str,
    payload: HeatmapCompareRequest,
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = compare_heatmap_service(session, experiment_id=experiment_id, payload=payload)
    return success_response(data=data)


@router.get(
    "/{experiment_id}/path-traces",
    summary="获取路径追踪数据",
    response_model=ApiResponse[PathTracesData],
)
def get_path_traces(
    experiment_id: str,
    params: PathTracesQueryParams = Depends(),
    _: str = Depends(request_id_header),
    __: str = Depends(experiment_id_header),
    session: Session = Depends(get_db_session),
):
    data = get_path_traces_service(session, experiment_id=experiment_id, params=params)
    return success_response(data=data)
