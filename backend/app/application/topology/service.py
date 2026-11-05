"""实验拓扑应用服务。

拓扑是实验启动前的基础配置，决定后续人群分配、出生点生成、决策出口
和分析统计能否正确关联。本服务采用“整包覆盖”语义：每次 PUT 都会替换
当前实验的区域和出口，并清理依赖旧拓扑的分配与出生点。
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from backend.app.application.common import raise_topology_validation_failed
from backend.app.application.allocation_spawn.service import invalidate_spawn_positions_cache
from backend.app.application.experiments.service import (
    ensure_experiment_editable,
    get_experiment_or_404,
    refresh_pre_run_status,
)
from backend.app.core.precision import round_coordinate
from backend.app.infrastructure.db.models import ExperimentExit, ExperimentZone
from backend.app.infrastructure.db.repositories import (
    ExperimentAgentRepository,
    ExperimentAllocationRepository,
    ExperimentExitRepository,
    ExperimentZoneRepository,
)
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.topology import TopologyUpsertRequest, TopologyUpsertResponseData

logger = get_logger(__name__)


def put_topology(
    session: Session,
    *,
    experiment_id: str,
    payload: TopologyUpsertRequest,
) -> TopologyUpsertResponseData:
    """覆盖写入实验拓扑。

    只允许在实验启动前修改。写入顺序是：先校验，再删除下游派生数据，
    最后写入新拓扑并刷新实验启动前状态，避免旧分配继续引用已经不存在的区域。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    ensure_experiment_editable(experiment)
    _validate_topology(payload)

    zone_repository = ExperimentZoneRepository(session)
    exit_repository = ExperimentExitRepository(session)
    allocation_repository = ExperimentAllocationRepository(session)
    agent_repository = ExperimentAgentRepository(session)

    # 拓扑一旦整体覆盖，旧分配和旧出生点都会失去语义，需要一并清空。
    agent_repository.delete_by_experiment_id(experiment.id)
    invalidate_spawn_positions_cache(experiment_pk=experiment.id)
    allocation_repository.delete_by_experiment_id(experiment.id)
    exit_repository.delete_by_experiment_id(experiment.id)
    zone_repository.delete_by_experiment_id(experiment.id)
    experiment.agent_id_prefix = None
    session.flush()

    zones = [
        ExperimentZone(
            experiment_id=experiment.id,
            zone_id=zone.zone_id,
            zone_name=zone.zone_name,
            zone_type=zone.zone_type,
            min_x=round_coordinate(zone.bounds.min_x),
            min_z=round_coordinate(zone.bounds.min_z),
            max_x=round_coordinate(zone.bounds.max_x),
            max_z=round_coordinate(zone.bounds.max_z),
            capacity=zone.capacity,
            is_entry_zone=zone.is_entry_zone,
            metadata_json=zone.metadata,
        )
        for zone in payload.zones
    ]
    zone_repository.add_all(zones)
    session.flush()

    exits = [
        ExperimentExit(
            experiment_id=experiment.id,
            exit_id=topology_exit.exit_id,
            source_zone_id=topology_exit.source_zone_id,
            target_zone_id=topology_exit.target_zone_id,
            exit_type=topology_exit.exit_type,
            position_x=round_coordinate(topology_exit.position.x),
            position_z=round_coordinate(topology_exit.position.z),
            pass_capacity_per_sec=topology_exit.pass_capacity_per_sec,
            metadata_json=topology_exit.metadata,
        )
        for topology_exit in payload.exits
    ]
    exit_repository.add_all(exits)
    refresh_pre_run_status(session, experiment)
    session.commit()
    logger.info(
        "拓扑写入完成: experiment_id=%s zone_count=%s exit_count=%s",
        experiment.experiment_id,
        len(payload.zones),
        len(payload.exits),
    )

    entry_zones = [zone.zone_id for zone in payload.zones if zone.is_entry_zone]
    return TopologyUpsertResponseData(
        zone_count=len(payload.zones),
        exit_count=len(payload.exits),
        entry_zones=entry_zones,
        validation_errors=[],
    )


def _validate_topology(payload: TopologyUpsertRequest) -> None:
    """校验拓扑内部一致性。

    当前后端把 `exit_id` 收口为同一实验内全局唯一，而不是只在
    `source_zone_id` 下唯一。这样决策结果、轨迹目标出口和统计聚合只携带
    `exit_id` 时不会产生歧义。
    """

    zone_ids = [zone.zone_id for zone in payload.zones]
    duplicate_zone_ids = _find_duplicates(zone_ids)
    if duplicate_zone_ids:
        message = f"拓扑中存在重复的区域编号: {', '.join(duplicate_zone_ids)}"
        raise_topology_validation_failed(message, validation_errors=[message])

    exit_ids = [topology_exit.exit_id for topology_exit in payload.exits]
    duplicate_exit_ids = _find_duplicates(exit_ids)
    if duplicate_exit_ids:
        message = f"拓扑中存在重复的出口编号: {', '.join(duplicate_exit_ids)}"
        raise_topology_validation_failed(message, validation_errors=[message])

    zone_id_set = set(zone_ids)
    if not any(zone.is_entry_zone for zone in payload.zones):
        message = "拓扑中必须至少存在一个 is_entry_zone=true 的入口区域"
        raise_topology_validation_failed(message, validation_errors=[message])

    invalid_zone_refs = sorted(
        {
            zone_id
            for topology_exit in payload.exits
            for zone_id in (topology_exit.source_zone_id, topology_exit.target_zone_id)
            if zone_id not in zone_id_set
        }
    )
    if invalid_zone_refs:
        message = f"出口引用了不存在的区域: {', '.join(invalid_zone_refs)}"
        raise_topology_validation_failed(message, validation_errors=[message])


def _find_duplicates(items: list[str]) -> list[str]:
    seen: set[str] = set()
    duplicates: set[str] = set()

    for item in items:
        if item in seen:
            duplicates.add(item)
        seen.add(item)

    return sorted(duplicates)
