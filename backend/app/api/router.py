"""统一聚合各业务路由。"""

from fastapi import APIRouter

from backend.app.api.routes import (
    allocation_spawn,
    analytics,
    crowd_types,
    decisions,
    experiments,
    topology,
    trajectories,
)

api_router = APIRouter()
api_router.include_router(crowd_types.router)
api_router.include_router(experiments.router)
api_router.include_router(topology.router)
api_router.include_router(allocation_spawn.router)
api_router.include_router(decisions.router)
api_router.include_router(trajectories.router)
api_router.include_router(analytics.router)
