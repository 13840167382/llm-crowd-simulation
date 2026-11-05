"""人群分配与出生点应用服务。

本文件负责启动前“人群怎么来、出生在哪里”的业务编排。
人群分配会固化模板快照，出生点生成则基于 seed 做确定性随机，
并同时考虑区域容量、入口区域、点扩散、出口避让和最小社交距离。
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from random import Random, SystemRandom
from typing import Any

from sqlalchemy.orm import Session

from backend.app.application.common import (
    decimal_to_float,
    raise_crowd_allocation_validation_failed,
    raise_experiment_status_conflict,
)
from backend.app.application.crowd_types.service import build_template_snapshot
from backend.app.application.experiments.service import (
    ensure_experiment_editable,
    get_experiment_or_404,
    refresh_pre_run_status,
)
from backend.app.core.config import get_settings
from backend.app.core.precision import round_coordinate
from backend.app.domain.enums import DistributionMode, ExperimentStatus
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import ExperimentAgent, ExperimentAllocation, ExperimentExit, ExperimentZone
from backend.app.infrastructure.db.repositories import (
    CrowdTypeTemplateRepository,
    ExperimentAgentRepository,
    ExperimentAllocationRepository,
    ExperimentExitRepository,
    ExperimentZoneRepository,
)
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.allocation import (
    CrowdAllocationGroup,
    CrowdAllocationGroupSummary,
    CrowdAllocationRequest,
    CrowdAllocationResponseData,
    SpawnPositionsRequest,
    SpawnPositionsResponseData,
    SpawnedAgent,
)
from backend.app.schemas.topology import Position2D

MAX_TOTAL_AGENT_COUNT = 10_000
MIN_DISTANCE_RELAX_FACTOR = 0.3
PLACEMENT_ATTEMPTS_PER_AGENT = 200
BEST_EFFORT_ATTEMPTS = 80
logger = get_logger(__name__)


@dataclass(frozen=True)
class SpawnBucket:
    """同一批出生点的目标生成范围。

    一个 bucket 表示“某类人群中的若干人要放到同一区域或同一个扩散点附近”。
    `zone_fixed`、`random_entry`、`zone_weighted` 和 `point_spread` 最终都会被拆成
    若干 bucket，再统一走坐标采样逻辑。
    """

    zone_id: str
    count: int
    center_x: float | None = None
    center_z: float | None = None
    radius: float | None = None


def put_crowd_allocation(
    session: Session,
    *,
    experiment_id: str,
    payload: CrowdAllocationRequest,
) -> CrowdAllocationResponseData:
    """覆盖写入实验人群分配。

    分配依赖拓扑，因此必须先有区域。每次覆盖分配时都会删除旧出生点和
    对应 Redis 缓存，因为旧坐标是基于旧分配生成的，继续复用会造成数量或类型不一致。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    ensure_experiment_editable(experiment)

    zone_repository = ExperimentZoneRepository(session)
    zones = zone_repository.list_by_experiment_id(experiment.id)
    if not zones:
        raise_crowd_allocation_validation_failed("当前实验尚未配置拓扑，无法提交人群分配")

    zone_id_set = {zone.zone_id for zone in zones}
    entry_zone_ids = {zone.zone_id for zone in zones if zone.is_entry_zone}

    _validate_group_duplicates(payload.groups)
    _validate_total_count(payload.groups)
    template_map = _load_template_map(session, payload)
    _validate_distribution_references(payload.groups, zone_id_set=zone_id_set, entry_zone_ids=entry_zone_ids)

    allocation_repository = ExperimentAllocationRepository(session)
    agent_repository = ExperimentAgentRepository(session)
    # 人群分配被整体覆盖时，基于旧分配生成的出生点也必须作废。
    agent_repository.delete_by_experiment_id(experiment.id)
    invalidate_spawn_positions_cache(experiment_pk=experiment.id)
    allocation_repository.delete_by_experiment_id(experiment.id)
    session.flush()

    allocations = [
        ExperimentAllocation(
            experiment_id=experiment.id,
            type_id=group.type_id,
            count=group.count,
            distribution_mode=DistributionMode(group.distribution.mode),
            distribution_config=group.distribution.model_dump(mode="json"),
            template_snapshot=build_template_snapshot(template_map[group.type_id]),
        )
        for group in payload.groups
    ]
    allocation_repository.add_all(allocations)
    experiment.agent_id_prefix = payload.agent_id_prefix or _default_agent_id_prefix(experiment.experiment_id)
    session.flush()
    refresh_pre_run_status(session, experiment)
    session.commit()
    logger.info(
        "人群分配写入完成: experiment_id=%s group_count=%s total_count=%s",
        experiment.experiment_id,
        len(payload.groups),
        sum(group.count for group in payload.groups),
    )
    return _build_allocation_response(experiment_id=experiment.experiment_id, status=experiment.status, allocations=allocations)


def get_crowd_allocation(session: Session, *, experiment_id: str) -> CrowdAllocationResponseData:
    """获取实验人群分配。"""

    experiment = get_experiment_or_404(session, experiment_id)
    allocation_repository = ExperimentAllocationRepository(session)
    allocations = allocation_repository.list_by_experiment_id(experiment.id)
    return _build_allocation_response(
        experiment_id=experiment.experiment_id,
        status=experiment.status,
        allocations=allocations,
    )


def create_spawn_positions(
    session: Session,
    *,
    experiment_id: str,
    payload: SpawnPositionsRequest | None,
) -> SpawnPositionsResponseData:
    """按当前分配方案生成确定性的出生点。

    生成策略遵循三个原则：
    1. 同一实验同一 seed 返回稳定结果，便于 Unity 重试和复现；
    2. 如果已有出生点且 seed 一致，直接回放数据库结果；
    3. 如果 seed 改变，则清空旧坐标并重新生成。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    if experiment.status != ExperimentStatus.CONFIGURED:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态为 `{experiment.status}`，无法生成出生点",
            data={"status": experiment.status},
        )

    agent_repository = ExperimentAgentRepository(session)
    existing_agents = agent_repository.list_by_experiment_id(experiment.id)
    requested_seed = payload.seed if payload is not None else None
    if requested_seed is not None:
        cached_response = _get_cached_spawn_positions_response(experiment_pk=experiment.id, seed=requested_seed)
        if cached_response is not None:
            logger.info("出生点命中 Redis 热点缓存: experiment_id=%s seed=%s", experiment.experiment_id, requested_seed)
            return cached_response

    if existing_agents:
        existing_seed = existing_agents[0].generation_seed or 0
        # 同实验、同 seed 视为幂等回放，直接返回已有结果。
        if requested_seed is None or requested_seed == existing_seed:
            logger.info("出生点命中幂等回放: experiment_id=%s seed=%s", experiment.experiment_id, existing_seed)
            response = _build_spawn_positions_response(seed=existing_seed, agents=existing_agents, warnings=[])
            _cache_spawn_positions_response(experiment_pk=experiment.id, seed=existing_seed, response=response)
            return response

        # 用户显式换 seed 时，视为需要重新排布出生点；先删库再生成，避免同一实验出现两套坐标。
        agent_repository.delete_by_experiment_id(experiment.id)
        invalidate_spawn_positions_cache(experiment_pk=experiment.id)
        session.flush()

    allocation_repository = ExperimentAllocationRepository(session)
    allocations = allocation_repository.list_by_experiment_id(experiment.id)
    if not allocations:
        raise_crowd_allocation_validation_failed("当前实验尚未配置人群分配，无法生成出生点")

    zone_repository = ExperimentZoneRepository(session)
    zones = zone_repository.list_by_experiment_id(experiment.id)
    if not zones:
        raise_crowd_allocation_validation_failed("当前实验尚未配置拓扑，无法生成出生点")

    exit_repository = ExperimentExitRepository(session)
    exits = exit_repository.list_by_experiment_id(experiment.id)

    seed = requested_seed if requested_seed is not None else _generate_seed()
    rng = Random(seed)
    prefix = experiment.agent_id_prefix or _default_agent_id_prefix(experiment.experiment_id)
    warnings: list[str] = []
    generated_agents = _generate_agents_for_allocations(
        allocations=allocations,
        zones=zones,
        exits=exits,
        prefix=prefix,
        rng=rng,
        generation_seed=seed,
        warnings=warnings,
        experiment_pk=experiment.id,
    )

    agent_repository.add_all(generated_agents)
    session.commit()
    response = _build_spawn_positions_response(seed=seed, agents=generated_agents, warnings=warnings)
    _cache_spawn_positions_response(experiment_pk=experiment.id, seed=seed, response=response)
    logger.info(
        "出生点生成完成: experiment_id=%s seed=%s total_count=%s warning_count=%s",
        experiment.experiment_id,
        seed,
        len(generated_agents),
        len(_deduplicate_messages(warnings)),
    )
    return response


def _load_template_map(session: Session, payload: CrowdAllocationRequest) -> dict[str, Any]:
    repository = CrowdTypeTemplateRepository(session)
    type_ids = [group.type_id for group in payload.groups]
    templates = repository.list_by_type_ids(type_ids)
    template_map = {template.type_id: template for template in templates}

    missing = sorted(set(type_ids) - set(template_map))
    if missing:
        message = f"以下人群类型模板不存在: {', '.join(missing)}"
        raise_crowd_allocation_validation_failed(message)

    return template_map


def _validate_group_duplicates(groups: list[CrowdAllocationGroup]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()

    for group in groups:
        if group.type_id in seen:
            duplicates.add(group.type_id)
        seen.add(group.type_id)

    if duplicates:
        message = f"人群分配中存在重复的 type_id: {', '.join(sorted(duplicates))}"
        raise_crowd_allocation_validation_failed(message)


def _validate_total_count(groups: list[CrowdAllocationGroup]) -> None:
    total_count = sum(group.count for group in groups)
    if total_count > MAX_TOTAL_AGENT_COUNT:
        message = f"当前人群总数 {total_count} 超过上限 {MAX_TOTAL_AGENT_COUNT}"
        raise_crowd_allocation_validation_failed(message, validation_errors=[message])


def _validate_distribution_references(
    groups: list[CrowdAllocationGroup],
    *,
    zone_id_set: set[str],
    entry_zone_ids: set[str],
) -> None:
    """校验分配模式引用的区域是否存在。

    这里不只检查 `zone_weights`，也检查固定区域和入口随机模式。
    这样可以把拓扑错误尽早暴露在分配阶段，而不是等出生点生成时才失败。
    """

    for group in groups:
        distribution = group.distribution

        if distribution.mode == DistributionMode.ZONE_WEIGHTED.value:
            invalid_zone_ids = sorted(
                {
                    zone_weight.zone_id
                    for zone_weight in distribution.zone_weights
                    if zone_weight.zone_id not in zone_id_set
                }
            )
            if invalid_zone_ids:
                message = f"分组 `{group.type_id}` 引用了不存在的区域: {', '.join(invalid_zone_ids)}"
                raise_crowd_allocation_validation_failed(message, validation_errors=[message])

        if distribution.mode == DistributionMode.ZONE_FIXED.value and distribution.fixed_zone_id not in zone_id_set:
            message = f"分组 `{group.type_id}` 固定区域 `{distribution.fixed_zone_id}` 不存在"
            raise_crowd_allocation_validation_failed(message, validation_errors=[message])

        if distribution.mode == DistributionMode.RANDOM_ENTRY.value and not entry_zone_ids:
            message = f"分组 `{group.type_id}` 采用 random_entry 模式时，拓扑中必须至少存在一个入口区域"
            raise_crowd_allocation_validation_failed(message, validation_errors=[message])


def _build_allocation_response(
    *,
    experiment_id: str,
    status: object,
    allocations: list[ExperimentAllocation],
) -> CrowdAllocationResponseData:
    summaries = [
        CrowdAllocationGroupSummary(
            type_id=allocation.type_id,
            display_name=str(allocation.template_snapshot["display_name"]),
            color_hex=str(allocation.template_snapshot["color_hex"]),
            count=allocation.count,
            mode=allocation.distribution_mode,
        )
        for allocation in allocations
    ]
    total_count = sum(allocation.count for allocation in allocations)

    return CrowdAllocationResponseData(
        experiment_id=experiment_id,
        status=status,
        total_count=total_count,
        group_count=len(allocations),
        group_summary=summaries,
        validation_errors=[],
    )


def _generate_agents_for_allocations(
    *,
    allocations: list[ExperimentAllocation],
    zones: list[ExperimentZone],
    exits: list[ExperimentExit],
    prefix: str,
    rng: Random,
    generation_seed: int,
    warnings: list[str],
    experiment_pk: int,
) -> list[ExperimentAgent]:
    """根据分配记录生成完整 agent 列表。

    每个 allocation 先拆成若干 `SpawnBucket`，再在 bucket 内做空间采样。
    agent_id 按全局顺序编号，确保不同人群类型之间也不会重复。
    """

    zone_map = {zone.zone_id: zone for zone in zones}
    exit_map = _group_exits_by_zone(exits)
    total_count = sum(allocation.count for allocation in allocations)
    width = max(4, len(str(total_count)))
    global_order = 1
    agents: list[ExperimentAgent] = []

    for allocation in allocations:
        template_snapshot = allocation.template_snapshot
        social_distance = max(float(template_snapshot.get("social_distance") or 0.0), 0.01)
        distribution_config = allocation.distribution_config
        buckets = _build_spawn_buckets(
            allocation=allocation,
            distribution_config=distribution_config,
            zones=zones,
            zone_map=zone_map,
            rng=rng,
            warnings=warnings,
        )
        # 各 bucket 单独维护已放置坐标，保证同一区域内的最小间距控制连续生效。
        placed_positions_by_zone: dict[str, list[tuple[float, float]]] = defaultdict(list)
        avoid_exits = bool(distribution_config.get("avoid_exits", True))

        for bucket in buckets:
            zone = zone_map[bucket.zone_id]
            bucket_positions = _generate_positions_for_bucket(
                zone=zone,
                exits=exit_map.get(bucket.zone_id, []) if avoid_exits else [],
                count=bucket.count,
                min_distance=social_distance,
                rng=rng,
                existing_positions=placed_positions_by_zone[bucket.zone_id],
                center_x=bucket.center_x,
                center_z=bucket.center_z,
                radius=bucket.radius,
                warnings=warnings,
                warning_scope=f"类型 `{allocation.type_id}` 在区域 `{bucket.zone_id}`",
            )
            placed_positions_by_zone[bucket.zone_id].extend(bucket_positions)

            for x, z in bucket_positions:
                agent_id = f"{prefix}_{global_order:0{width}d}"
                agents.append(
                    ExperimentAgent(
                        experiment_id=experiment_pk,
                        agent_id=agent_id,
                        crowd_type=allocation.type_id,
                        spawn_zone_id=bucket.zone_id,
                        spawn_x=round_coordinate(x),
                        spawn_z=round_coordinate(z),
                        rotation_y=round(rng.uniform(0.0, 360.0), 1),
                        spawn_order=global_order,
                        generation_seed=generation_seed,
                    )
                )
                global_order += 1

    return agents


def _build_spawn_buckets(
    *,
    allocation: ExperimentAllocation,
    distribution_config: dict[str, Any],
    zones: list[ExperimentZone],
    zone_map: dict[str, ExperimentZone],
    rng: Random,
    warnings: list[str],
) -> list[SpawnBucket]:
    """把不同分布模式统一转换为出生点 bucket。

    转换后，后面的坐标生成逻辑不用再关心原始模式，只处理“区域 + 数量 + 可选中心点/半径”。
    """

    mode = str(distribution_config["mode"])

    if mode == DistributionMode.ZONE_FIXED.value:
        return [SpawnBucket(zone_id=str(distribution_config["fixed_zone_id"]), count=allocation.count)]

    if mode == DistributionMode.RANDOM_ENTRY.value:
        entry_zones = [zone.zone_id for zone in zones if zone.is_entry_zone]
        counts = _sample_bucket_counts(
            count=allocation.count,
            bucket_keys=entry_zones,
            weights=[1.0 for _ in entry_zones],
            rng=rng,
        )
        return [SpawnBucket(zone_id=zone_id, count=bucket_count) for zone_id, bucket_count in counts if bucket_count > 0]

    if mode == DistributionMode.ZONE_WEIGHTED.value:
        zone_weights = distribution_config.get("zone_weights", [])
        bucket_keys = [str(item["zone_id"]) for item in zone_weights]
        weights = [float(item["weight"]) for item in zone_weights]
        counts = _sample_bucket_counts(count=allocation.count, bucket_keys=bucket_keys, weights=weights, rng=rng)
        return [SpawnBucket(zone_id=zone_id, count=bucket_count) for zone_id, bucket_count in counts if bucket_count > 0]

    if mode == DistributionMode.POINT_SPREAD.value:
        spread_radius = float(distribution_config.get("spread_radius") or 1.0)
        spawn_points = distribution_config.get("spawn_points", [])
        point_counts = _sample_bucket_counts(
            count=allocation.count,
            bucket_keys=list(range(len(spawn_points))),
            weights=[float(item["weight"]) for item in spawn_points],
            rng=rng,
        )
        buckets: list[SpawnBucket] = []
        for point_index, bucket_count in point_counts:
            if bucket_count <= 0:
                continue

            point = spawn_points[point_index]
            center_x = round_coordinate(point["x"])
            center_z = round_coordinate(point["z"])
            zone = _find_zone_for_point(zones, center_x, center_z)
            if zone is None:
                zone = _find_nearest_zone(zones, center_x, center_z)
                # 点扩散中心落在拓扑外时，退回最近区域，同时保留 warning 供联调排查。
                warnings.append(
                    f"类型 `{allocation.type_id}` 的扩散点 ({center_x:.3f}, {center_z:.3f}) 超出拓扑边界，已回退到最近区域 `{zone.zone_id}`"
                )

            buckets.append(
                SpawnBucket(
                    zone_id=zone.zone_id,
                    count=bucket_count,
                    center_x=center_x,
                    center_z=center_z,
                    radius=spread_radius,
                )
            )
        return buckets

    message = f"类型 `{allocation.type_id}` 使用了不支持的分布模式 `{mode}`"
    raise_crowd_allocation_validation_failed(message, validation_errors=[message])


def _sample_bucket_counts(
    *,
    count: int,
    bucket_keys: list[Any],
    weights: list[float],
    rng: Random,
) -> list[tuple[Any, int]]:
    """按权重把人数分配到多个 bucket。

    这里使用逐人随机采样而不是简单四舍五入，能保持总人数严格等于输入 count，
    也能让同一 seed 下结果稳定可复现。
    """

    counts = {key: 0 for key in bucket_keys}
    total_weight = sum(weights)
    cumulative: list[float] = []
    running_weight = 0.0
    # 这里使用累计概率采样，把离散权重分布稳定映射成具体人数分桶。
    for weight in weights:
        running_weight += weight / total_weight
        cumulative.append(running_weight)

    for _ in range(count):
        sample = rng.random()
        for index, threshold in enumerate(cumulative):
            if sample <= threshold:
                counts[bucket_keys[index]] += 1
                break
        else:
            counts[bucket_keys[-1]] += 1

    return [(key, counts[key]) for key in bucket_keys]


def _group_exits_by_zone(exits: list[ExperimentExit]) -> dict[str, list[tuple[float, float]]]:
    exit_map: dict[str, list[tuple[float, float]]] = defaultdict(list)
    for topology_exit in exits:
        exit_map[topology_exit.source_zone_id].append(
            (
                decimal_to_float(topology_exit.position_x) or 0.0,
                decimal_to_float(topology_exit.position_z) or 0.0,
            )
        )
    return exit_map


def _generate_positions_for_bucket(
    *,
    zone: ExperimentZone,
    exits: list[tuple[float, float]],
    count: int,
    min_distance: float,
    rng: Random,
    existing_positions: list[tuple[float, float]],
    center_x: float | None,
    center_z: float | None,
    radius: float | None,
    warnings: list[str],
    warning_scope: str,
) -> list[tuple[float, float]]:
    """在一个 bucket 内生成指定数量的坐标。

    先尝试满足模板中的社交距离；如果区域太小或人数太多，会逐步放宽距离。
    放宽到下限仍失败时，退化为 best-effort，保证接口能返回完整人数并附带 warning。
    """

    positions: list[tuple[float, float]] = []
    target_distance = max(min_distance, 0.01)
    minimum_distance = max(target_distance * MIN_DISTANCE_RELAX_FACTOR, 0.01)
    relaxed = False
    warned_best_effort = False

    while len(positions) < count:
        candidate = _find_valid_candidate(
            zone=zone,
            exits=exits,
            target_distance=target_distance,
            rng=rng,
            existing_positions=existing_positions + positions,
            center_x=center_x,
            center_z=center_z,
            radius=radius,
        )
        if candidate is not None:
            positions.append(candidate)
            continue

        if target_distance > minimum_distance:
            # 先逐步放宽最小间距，再尝试继续找合法点，避免一次性退化得过于激进。
            target_distance = max(minimum_distance, round(target_distance * 0.8, 4))
            if not relaxed:
                warnings.append(
                    f"{warning_scope} 的最小间距不足，已从 {min_distance:.3f} 逐步放宽到不低于 {minimum_distance:.3f}"
                )
                relaxed = True
            continue

        # 已经无法满足最小间距时，退化为“尽力找最不拥挤的位置”，保证结果仍可返回。
        fallback = _find_best_effort_candidate(
            zone=zone,
            exits=exits,
            rng=rng,
            existing_positions=existing_positions + positions,
            center_x=center_x,
            center_z=center_z,
            radius=radius,
        )
        positions.append(fallback)
        if not warned_best_effort:
            warnings.append(f"{warning_scope} 已按尽力策略生成部分出生点，局部间距可能小于期望值")
            warned_best_effort = True

    return positions


def _find_valid_candidate(
    *,
    zone: ExperimentZone,
    exits: list[tuple[float, float]],
    target_distance: float,
    rng: Random,
    existing_positions: list[tuple[float, float]],
    center_x: float | None,
    center_z: float | None,
    radius: float | None,
) -> tuple[float, float] | None:
    """在有限次数内寻找满足距离约束的候选点。"""

    for _ in range(PLACEMENT_ATTEMPTS_PER_AGENT):
        candidate = _sample_candidate_point(
            zone=zone,
            rng=rng,
            center_x=center_x,
            center_z=center_z,
            radius=radius,
        )
        if _is_candidate_valid(candidate, existing_positions, exits, target_distance):
            return candidate
    return None


def _find_best_effort_candidate(
    *,
    zone: ExperimentZone,
    exits: list[tuple[float, float]],
    rng: Random,
    existing_positions: list[tuple[float, float]],
    center_x: float | None,
    center_z: float | None,
    radius: float | None,
) -> tuple[float, float]:
    """找不到严格合法点时，选择与已有点/出口距离最大的候选点。"""

    best_candidate = _sample_candidate_point(zone=zone, rng=rng, center_x=center_x, center_z=center_z, radius=radius)
    best_score = _candidate_score(best_candidate, existing_positions, exits)

    for _ in range(BEST_EFFORT_ATTEMPTS):
        candidate = _sample_candidate_point(
            zone=zone,
            rng=rng,
            center_x=center_x,
            center_z=center_z,
            radius=radius,
        )
        score = _candidate_score(candidate, existing_positions, exits)
        if score > best_score:
            best_candidate = candidate
            best_score = score

    return best_candidate


def _sample_candidate_point(
    *,
    zone: ExperimentZone,
    rng: Random,
    center_x: float | None,
    center_z: float | None,
    radius: float | None,
) -> tuple[float, float]:
    """按 bucket 配置采样一个候选点。

    没有中心点时在整个区域矩形内均匀采样；有中心点时在圆形范围内采样，
    并把超出区域的点重新采样或夹回区域边界。
    """

    min_x = decimal_to_float(zone.min_x) or 0.0
    max_x = decimal_to_float(zone.max_x) or 0.0
    min_z = decimal_to_float(zone.min_z) or 0.0
    max_z = decimal_to_float(zone.max_z) or 0.0

    if center_x is None or center_z is None or radius is None:
        return (
            rng.uniform(min_x, max_x),
            rng.uniform(min_z, max_z),
        )

    for _ in range(PLACEMENT_ATTEMPTS_PER_AGENT):
        angle = rng.uniform(0.0, math.tau)
        distance = radius * math.sqrt(rng.random())
        candidate = (
            center_x + distance * math.cos(angle),
            center_z + distance * math.sin(angle),
        )
        if _is_point_inside_zone(zone, *candidate):
            return candidate

    return (
        min(max(center_x, min_x), max_x),
        min(max(center_z, min_z), max_z),
    )


def _is_candidate_valid(
    candidate: tuple[float, float],
    existing_positions: list[tuple[float, float]],
    exits: list[tuple[float, float]],
    target_distance: float,
) -> bool:
    if any(_distance(candidate, other) < target_distance for other in existing_positions):
        return False
    if any(_distance(candidate, exit_position) < target_distance for exit_position in exits):
        return False
    return True


def _candidate_score(
    candidate: tuple[float, float],
    existing_positions: list[tuple[float, float]],
    exits: list[tuple[float, float]],
) -> float:
    distances = [_distance(candidate, other) for other in existing_positions]
    distances.extend(_distance(candidate, exit_position) for exit_position in exits)
    return min(distances) if distances else float("inf")


def _distance(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _is_point_inside_zone(zone: ExperimentZone, x: float, z: float) -> bool:
    min_x = decimal_to_float(zone.min_x) or 0.0
    max_x = decimal_to_float(zone.max_x) or 0.0
    min_z = decimal_to_float(zone.min_z) or 0.0
    max_z = decimal_to_float(zone.max_z) or 0.0
    return min_x <= x <= max_x and min_z <= z <= max_z


def _find_zone_for_point(zones: list[ExperimentZone], x: float, z: float) -> ExperimentZone | None:
    for zone in zones:
        if _is_point_inside_zone(zone, x, z):
            return zone
    return None


def _find_nearest_zone(zones: list[ExperimentZone], x: float, z: float) -> ExperimentZone:
    return min(zones, key=lambda zone: _distance_to_zone(zone, x, z))


def _distance_to_zone(zone: ExperimentZone, x: float, z: float) -> float:
    min_x = decimal_to_float(zone.min_x) or 0.0
    max_x = decimal_to_float(zone.max_x) or 0.0
    min_z = decimal_to_float(zone.min_z) or 0.0
    max_z = decimal_to_float(zone.max_z) or 0.0
    dx = max(min_x - x, 0.0, x - max_x)
    dz = max(min_z - z, 0.0, z - max_z)
    return math.hypot(dx, dz)


def _build_spawn_positions_response(
    *,
    seed: int,
    agents: list[ExperimentAgent],
    warnings: list[str],
) -> SpawnPositionsResponseData:
    return SpawnPositionsResponseData(
        seed=seed,
        total_count=len(agents),
        agents=[
            SpawnedAgent(
                agent_id=agent.agent_id,
                type_id=agent.crowd_type,
                zone_id=agent.spawn_zone_id,
                position=Position2D(
                    x=decimal_to_float(agent.spawn_x) or 0.0,
                    z=decimal_to_float(agent.spawn_z) or 0.0,
                ),
                rotation_y=decimal_to_float(agent.rotation_y),
            )
            for agent in agents
        ],
        warnings=_deduplicate_messages(warnings),
    )


def _deduplicate_messages(messages: list[str]) -> list[str]:
    return list(dict.fromkeys(messages))


def _default_agent_id_prefix(experiment_id: str) -> str:
    return experiment_id[:8]


def _generate_seed() -> int:
    return SystemRandom().randint(1, 2_147_483_647)


def invalidate_spawn_positions_cache(*, experiment_pk: int) -> int:
    """清理实验对应的出生点 Redis 缓存。

    拓扑或分配发生变化时必须调用它，否则同一个 seed 可能读到旧拓扑/旧分配下的坐标。
    """

    deleted = get_redis_client().delete_by_pattern(
        "spawn-positions",
        "response",
        "exp",
        str(experiment_pk),
        "*",
    )
    if deleted > 0:
        logger.info("出生点 Redis 缓存已失效: experiment_pk=%s deleted=%s", experiment_pk, deleted)
    return deleted


def _get_cached_spawn_positions_response(
    *,
    experiment_pk: int,
    seed: int,
) -> SpawnPositionsResponseData | None:
    payload = get_redis_client().get_json(
        "spawn-positions",
        "response",
        "exp",
        str(experiment_pk),
        f"seed:{seed}",
    )
    if not isinstance(payload, dict):
        return None
    return SpawnPositionsResponseData.model_validate(payload)


def _cache_spawn_positions_response(
    *,
    experiment_pk: int,
    seed: int,
    response: SpawnPositionsResponseData,
) -> None:
    ttl_seconds = max(get_settings().spawn_positions_cache_ttl_seconds, 0)
    if ttl_seconds <= 0:
        return
    get_redis_client().set_json(
        "spawn-positions",
        "response",
        "exp",
        str(experiment_pk),
        f"seed:{seed}",
        value=response.model_dump(mode="json"),
        ttl_seconds=ttl_seconds,
    )
