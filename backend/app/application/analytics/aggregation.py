"""分钟级分析聚合同步刷新服务。

运行期轨迹点和决策明细写入频率较高，分析接口不适合每次都扫描原始表。
本模块按“受影响的 simulation_time_ms”定位分钟桶，先删除对应桶的旧聚合，再从原始表
重算并写回 *_metric_1m 表。轨迹查询还会额外生成 1 秒采样点，用于历史回放和 compact 查询。

这些刷新函数默认在调用方的事务中执行，不主动 commit；service 层负责决定何时提交。
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from backend.app.application.common import decimal_to_float
from backend.app.core.config import get_settings
from backend.app.core.precision import round_coordinate
from backend.app.infrastructure.db.models import (
    AgentDecision,
    AgentMetric1m,
    DecisionMetric1m,
    ExitMetric1m,
    ExperimentAgent,
    ExperimentExit,
    ExperimentZone,
    TrajectoryPoint,
    TrajectoryPointSampled1s,
    ZoneMetric1m,
)
from backend.app.infrastructure.db.repositories import (
    AgentMetric1mRepository,
    DecisionMetric1mRepository,
    ExitMetric1mRepository,
    TrajectoryPointSampledRepository,
    ZoneMetric1mRepository,
)
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)

BUCKET_SIZE_MS = 60_000


def refresh_decision_metrics_for_times(
    session: Session,
    *,
    experiment_pk: int,
    simulation_time_ms_values: list[int],
) -> None:
    """根据受影响时间片重算决策分钟聚合。

    决策聚合同时生成全体人群行和按 crowd_type 分组的行，分析接口可以直接按过滤条件选择。
    """

    bucket_starts = _resolve_bucket_starts(simulation_time_ms_values)
    if not bucket_starts:
        return

    # 聚合刷新采用 delete + rebuild，避免增量更新在重复上报、重放和清理补偿场景下累加出错。
    session.execute(
        delete(DecisionMetric1m).where(
            DecisionMetric1m.experiment_id == experiment_pk,
            DecisionMetric1m.bucket_start_ms.in_(bucket_starts),
        )
    )

    decisions = _list_decisions_for_buckets(
        session,
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
    )
    rows = _build_decision_metric_rows(
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
        decisions=decisions,
    )
    if rows:
        DecisionMetric1mRepository(session).add_all(rows)

    logger.info(
        "分钟决策聚合刷新完成: experiment_pk=%s bucket_count=%s row_count=%s",
        experiment_pk,
        len(bucket_starts),
        len(rows),
    )


def refresh_agent_metrics_for_times(
    session: Session,
    *,
    experiment_pk: int,
    simulation_time_ms_values: list[int],
) -> None:
    """按受影响时间桶刷新人物概览聚合。

    人物概览依赖出生点总人数、轨迹活跃人数和决策停留时长，因此需要同时读取 agents、
    trajectory_points 和 agent_decisions。
    """

    bucket_starts = _resolve_bucket_starts(simulation_time_ms_values)
    if not bucket_starts:
        return

    session.execute(
        delete(AgentMetric1m).where(
            AgentMetric1m.experiment_id == experiment_pk,
            AgentMetric1m.bucket_start_ms.in_(bucket_starts),
        )
    )

    agents = list(
        session.scalars(
            select(ExperimentAgent)
            .where(ExperimentAgent.experiment_id == experiment_pk)
            .order_by(ExperimentAgent.agent_id)
        )
    )
    points = _list_points_for_bucket_refresh(
        session,
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
    )
    decisions = _list_decisions_for_buckets(
        session,
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
    )
    rows = _build_agent_metric_rows(
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
        agents=agents,
        points=points,
        decisions=decisions,
    )
    if rows:
        AgentMetric1mRepository(session).add_all(rows)

    logger.info(
        "分钟级人物概览刷新完成: experiment_pk=%s bucket_count=%s row_count=%s",
        experiment_pk,
        len(bucket_starts),
        len(rows),
    )


def refresh_trajectory_metrics_for_times(
    session: Session,
    *,
    experiment_pk: int,
    simulation_time_ms_values: list[int],
) -> None:
    """根据受影响时间片重算区域与出口分钟聚合。

    区域聚合关注人数峰值、进出次数、排队峰值和停留时长；出口聚合关注通行量和等待时长。
    为了正确识别跨桶迁移，读取轨迹时会包含目标桶之前的同一实验轨迹点。
    """

    bucket_starts = _resolve_bucket_starts(simulation_time_ms_values)
    if not bucket_starts:
        return

    session.execute(
        delete(ZoneMetric1m).where(
            ZoneMetric1m.experiment_id == experiment_pk,
            ZoneMetric1m.bucket_start_ms.in_(bucket_starts),
        )
    )
    session.execute(
        delete(ExitMetric1m).where(
            ExitMetric1m.experiment_id == experiment_pk,
            ExitMetric1m.bucket_start_ms.in_(bucket_starts),
        )
    )

    zones = list(
        session.scalars(
            select(ExperimentZone)
            .where(ExperimentZone.experiment_id == experiment_pk)
            .order_by(ExperimentZone.zone_id)
        )
    )
    exits = list(
        session.scalars(
            select(ExperimentExit)
            .where(ExperimentExit.experiment_id == experiment_pk)
            .order_by(ExperimentExit.exit_id)
        )
    )
    points = _list_points_for_bucket_refresh(
        session,
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
    )
    decisions = _list_decisions_for_buckets(
        session,
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
    )

    zone_rows = _build_zone_metric_rows(
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
        zones=zones,
        points=points,
        decisions=decisions,
    )
    exit_rows = _build_exit_metric_rows(
        experiment_pk=experiment_pk,
        bucket_starts=bucket_starts,
        exits=exits,
        points=points,
        decisions=decisions,
    )

    if zone_rows:
        ZoneMetric1mRepository(session).add_all(zone_rows)
    if exit_rows:
        ExitMetric1mRepository(session).add_all(exit_rows)

    logger.info(
        "分钟轨迹聚合刷新完成: experiment_pk=%s bucket_count=%s zone_rows=%s exit_rows=%s",
        experiment_pk,
        len(bucket_starts),
        len(zone_rows),
        len(exit_rows),
    )


def refresh_trajectory_samples_for_agents(
    session: Session,
    *,
    experiment_pk: int,
    agent_ids: list[str],
    simulation_time_ms_values: list[int],
) -> None:
    """按受影响 agent 刷新秒级采样轨迹。

    采样刷新从受影响时间前一个采样间隔开始，并保留 start_ms 之前最近的旧采样点作为 seed。
    这样同一 agent 在增量刷新时不会丢失上一段轨迹的连续性。
    """

    if not agent_ids or not simulation_time_ms_values:
        return

    settings = get_settings()
    sample_interval_ms = max(int(settings.trajectory_query_sample_interval_ms), 1)
    start_ms = max(min(int(value) for value in simulation_time_ms_values) - sample_interval_ms, 0)
    unique_agent_ids = sorted(set(agent_ids))
    existing_seed_points = _list_last_sampled_points_before_time(
        session,
        experiment_pk=experiment_pk,
        agent_ids=unique_agent_ids,
        time_ms=start_ms,
    )
    seed_by_agent = {point.agent_id: point for point in existing_seed_points}

    session.execute(
        delete(TrajectoryPointSampled1s).where(
            TrajectoryPointSampled1s.experiment_id == experiment_pk,
            TrajectoryPointSampled1s.agent_id.in_(unique_agent_ids),
            TrajectoryPointSampled1s.simulation_time_ms >= start_ms,
        )
    )

    raw_points = _list_points_for_agents_from_time(
        session,
        experiment_pk=experiment_pk,
        agent_ids=unique_agent_ids,
        time_ms=start_ms,
    )
    rows = _build_sampled_trajectory_rows(
        experiment_pk=experiment_pk,
        points=raw_points,
        sample_interval_ms=sample_interval_ms,
        seed_by_agent=seed_by_agent,
    )
    if rows:
        TrajectoryPointSampledRepository(session).add_all(rows)

    logger.info(
        "秒级采样轨迹刷新完成: experiment_pk=%s agent_count=%s start_ms=%s row_count=%s",
        experiment_pk,
        len(unique_agent_ids),
        start_ms,
        len(rows),
    )


def bucket_start_ms(simulation_time_ms: int) -> int:
    """将毫秒时间戳归并到分钟桶起点。"""

    return (int(simulation_time_ms) // BUCKET_SIZE_MS) * BUCKET_SIZE_MS


def _resolve_bucket_starts(simulation_time_ms_values: list[int]) -> list[int]:
    """把一组模拟时间归并成去重后的分钟桶起点。"""

    return sorted({bucket_start_ms(value) for value in simulation_time_ms_values})


def _list_decisions_for_buckets(
    session: Session,
    *,
    experiment_pk: int,
    bucket_starts: list[int],
) -> list[AgentDecision]:
    """读取覆盖目标分钟桶的决策明细，并过滤回精确桶集合。"""

    if not bucket_starts:
        return []

    start_ms = min(bucket_starts)
    end_ms = max(bucket_starts) + BUCKET_SIZE_MS - 1
    stmt = (
        select(AgentDecision)
        .where(
            AgentDecision.experiment_id == experiment_pk,
            AgentDecision.simulation_time_ms >= start_ms,
            AgentDecision.simulation_time_ms <= end_ms,
        )
        .order_by(AgentDecision.simulation_time_ms, AgentDecision.id)
    )
    decisions = list(session.scalars(stmt))
    bucket_set = set(bucket_starts)
    return [row for row in decisions if bucket_start_ms(int(row.simulation_time_ms)) in bucket_set]


def _list_points_for_bucket_refresh(
    session: Session,
    *,
    experiment_pk: int,
    bucket_starts: list[int],
) -> list[TrajectoryPoint]:
    """读取刷新区域/出口聚合所需的轨迹点。

    这里故意不从 min(bucket_starts) 开始读，因为区域迁移需要知道当前点之前所在区域。
    只读到最大目标桶上界，避免把未受影响的未来轨迹带入聚合。
    """

    if not bucket_starts:
        return []

    # 为了正确识别跨桶的区域迁移，刷新时读取到“受影响时间窗口上界”为止的全部轨迹点，
    # 这样第一条落入目标桶的轨迹点也能拿到自己的上一个状态。
    max_time_ms = max(bucket_starts) + BUCKET_SIZE_MS - 1
    stmt = (
        select(TrajectoryPoint)
        .where(
            TrajectoryPoint.experiment_id == experiment_pk,
            TrajectoryPoint.simulation_time_ms <= max_time_ms,
        )
        .order_by(TrajectoryPoint.agent_id, TrajectoryPoint.simulation_time_ms)
    )
    return list(session.scalars(stmt))


def _build_decision_metric_rows(
    *,
    experiment_pk: int,
    bucket_starts: list[int],
    decisions: list[AgentDecision],
) -> list[DecisionMetric1m]:
    """构造决策聚合行，包含全体人群和每个 crowd_type 两种粒度。"""

    rows: list[DecisionMetric1m] = []
    decisions_by_bucket: dict[int, list[AgentDecision]] = defaultdict(list)
    for decision in decisions:
        decisions_by_bucket[bucket_start_ms(int(decision.simulation_time_ms))].append(decision)

    for bucket in bucket_starts:
        bucket_decisions = decisions_by_bucket.get(bucket, [])
        if not bucket_decisions:
            continue

        rows.append(
            _build_single_decision_metric_row(
                experiment_pk=experiment_pk,
                bucket_start=bucket,
                crowd_type=None,
                decisions=bucket_decisions,
            )
        )

        decisions_by_type: dict[str, list[AgentDecision]] = defaultdict(list)
        for decision in bucket_decisions:
            decisions_by_type[decision.crowd_type].append(decision)

        for crowd_type, crowd_type_decisions in decisions_by_type.items():
            rows.append(
                _build_single_decision_metric_row(
                    experiment_pk=experiment_pk,
                    bucket_start=bucket,
                    crowd_type=crowd_type,
                    decisions=crowd_type_decisions,
                )
            )

    return rows


def _build_single_decision_metric_row(
    *,
    experiment_pk: int,
    bucket_start: int,
    crowd_type: str | None,
    decisions: list[AgentDecision],
) -> DecisionMetric1m:
    """构造单个分钟桶、单个 crowd_type 粒度的决策聚合行。"""

    latency_values = [int(item.llm_latency_ms) for item in decisions if item.llm_latency_ms is not None]
    return DecisionMetric1m(
        experiment_id=experiment_pk,
        bucket_start_ms=bucket_start,
        crowd_type=crowd_type,
        decided_count=len(decisions),
        llm_success_count=sum(1 for item in decisions if item.decision_source.value == "llm"),
        fallback_count=sum(1 for item in decisions if item.decision_source.value == "fallback"),
        override_count=sum(1 for item in decisions if bool(item.was_overridden)),
        avg_llm_latency_ms=_round_average_to_int(latency_values),
    )


def _build_agent_metric_rows(
    *,
    experiment_pk: int,
    bucket_starts: list[int],
    agents: list[ExperimentAgent],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
) -> list[AgentMetric1m]:
    """构造人物概览聚合行。

    active_count 来自该分钟桶内出现过轨迹点的 agent；exited_count 是总人数减活跃人数，
    表示在该时间桶内已经不再出现的人员数量。
    """

    rows: list[AgentMetric1m] = []
    bucket_set = set(bucket_starts)
    total_count = len(agents)
    total_count_by_type: dict[str, int] = defaultdict(int)
    for agent in agents:
        total_count_by_type[agent.crowd_type] += 1

    active_agent_ids_by_bucket: dict[int, set[str]] = defaultdict(set)
    active_agent_ids_by_bucket_type: dict[int, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    speed_values_by_bucket: dict[int, list[float]] = defaultdict(list)
    speed_values_by_bucket_type: dict[int, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    stay_values_by_bucket: dict[int, list[int]] = defaultdict(list)
    stay_values_by_bucket_type: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))

    for point in points:
        point_bucket = bucket_start_ms(int(point.simulation_time_ms))
        if point_bucket not in bucket_set:
            continue
        active_agent_ids_by_bucket[point_bucket].add(point.agent_id)
        active_agent_ids_by_bucket_type[point_bucket][point.crowd_type].add(point.agent_id)
        speed = decimal_to_float(point.speed) or 0.0
        speed_values_by_bucket[point_bucket].append(speed)
        speed_values_by_bucket_type[point_bucket][point.crowd_type].append(speed)

    for decision in decisions:
        decision_bucket = bucket_start_ms(int(decision.simulation_time_ms))
        if decision_bucket not in bucket_set:
            continue
        stay_value = int(decision.stay_duration_ms)
        stay_values_by_bucket[decision_bucket].append(stay_value)
        stay_values_by_bucket_type[decision_bucket][decision.crowd_type].append(stay_value)

    for bucket in bucket_starts:
        active_count = len(active_agent_ids_by_bucket.get(bucket, set()))
        rows.append(
            AgentMetric1m(
                experiment_id=experiment_pk,
                bucket_start_ms=bucket,
                crowd_type=None,
                total_count=total_count,
                active_count=active_count,
                exited_count=max(total_count - active_count, 0),
                avg_speed=_round_average(speed_values_by_bucket.get(bucket, []), digits=2),
                avg_stay_ms=_round_average_to_int(stay_values_by_bucket.get(bucket, [])),
            )
        )

        for crowd_type in sorted(total_count_by_type):
            crowd_total = total_count_by_type[crowd_type]
            crowd_active = len(active_agent_ids_by_bucket_type.get(bucket, {}).get(crowd_type, set()))
            rows.append(
                AgentMetric1m(
                    experiment_id=experiment_pk,
                    bucket_start_ms=bucket,
                    crowd_type=crowd_type,
                    total_count=crowd_total,
                    active_count=crowd_active,
                    exited_count=max(crowd_total - crowd_active, 0),
                    avg_speed=_round_average(speed_values_by_bucket_type.get(bucket, {}).get(crowd_type, []), digits=2),
                    avg_stay_ms=_round_average_to_int(stay_values_by_bucket_type.get(bucket, {}).get(crowd_type, [])),
                )
            )

    return rows


def _build_zone_metric_rows(
    *,
    experiment_pk: int,
    bucket_starts: list[int],
    zones: list[ExperimentZone],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
) -> list[ZoneMetric1m]:
    """构造区域分钟聚合行。

    轨迹点提供每个时刻的区域人数和排队人数，决策明细提供当前区域停留时长，
    相邻轨迹点的 zone_id 变化用于推断区域进入和离开次数。
    """

    rows: list[ZoneMetric1m] = []
    bucket_set = set(bucket_starts)
    zone_capacity = {zone.zone_id: zone.capacity for zone in zones}

    point_counts: dict[int, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    point_counts_by_type: dict[int, dict[str, dict[str, dict[int, int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    )
    queue_counts: dict[int, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    queue_counts_by_type: dict[int, dict[str, dict[str, dict[int, int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    )
    entries: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    entries_by_type: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    exits: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    exits_by_type: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    stay_values: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    stay_values_by_type: dict[int, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )

    for point in points:
        point_bucket = bucket_start_ms(int(point.simulation_time_ms))
        if point_bucket not in bucket_set:
            continue

        simulation_time_ms = int(point.simulation_time_ms)
        point_counts[point_bucket][point.zone_id][simulation_time_ms] += 1
        point_counts_by_type[point_bucket][point.zone_id][point.crowd_type][simulation_time_ms] += 1
        if point.is_queuing:
            queue_counts[point_bucket][point.zone_id][simulation_time_ms] += 1
            queue_counts_by_type[point_bucket][point.zone_id][point.crowd_type][simulation_time_ms] += 1

    for decision in decisions:
        decision_bucket = bucket_start_ms(int(decision.simulation_time_ms))
        stay_values[decision_bucket][decision.current_zone_id].append(int(decision.stay_duration_ms))
        stay_values_by_type[decision_bucket][decision.current_zone_id][decision.crowd_type].append(
            int(decision.stay_duration_ms)
        )

    for agent_points in _group_points_by_agent(points).values():
        for previous, current in zip(agent_points, agent_points[1:]):
            current_bucket = bucket_start_ms(int(current.simulation_time_ms))
            if current_bucket not in bucket_set or previous.zone_id == current.zone_id:
                continue

            exits[current_bucket][previous.zone_id] += 1
            exits_by_type[current_bucket][previous.zone_id][previous.crowd_type] += 1
            entries[current_bucket][current.zone_id] += 1
            entries_by_type[current_bucket][current.zone_id][current.crowd_type] += 1

    for bucket in bucket_starts:
        candidate_zone_ids = set(point_counts[bucket]) | set(entries[bucket]) | set(exits[bucket]) | set(stay_values[bucket])
        for zone_id in sorted(candidate_zone_ids):
            rows.append(
                _build_single_zone_metric_row(
                    experiment_pk=experiment_pk,
                    bucket_start=bucket,
                    zone_id=zone_id,
                    crowd_type=None,
                    counts_by_time=point_counts[bucket].get(zone_id, {}),
                    queue_by_time=queue_counts[bucket].get(zone_id, {}),
                    total_entries=entries[bucket].get(zone_id, 0),
                    total_exits=exits[bucket].get(zone_id, 0),
                    stay_values=stay_values[bucket].get(zone_id, []),
                    capacity=zone_capacity.get(zone_id),
                )
            )

            crowd_type_keys = (
                set(point_counts_by_type[bucket].get(zone_id, {}))
                | set(entries_by_type[bucket].get(zone_id, {}))
                | set(exits_by_type[bucket].get(zone_id, {}))
                | set(stay_values_by_type[bucket].get(zone_id, {}))
            )
            for crowd_type in sorted(crowd_type_keys):
                rows.append(
                    _build_single_zone_metric_row(
                        experiment_pk=experiment_pk,
                        bucket_start=bucket,
                        zone_id=zone_id,
                        crowd_type=crowd_type,
                        counts_by_time=point_counts_by_type[bucket].get(zone_id, {}).get(crowd_type, {}),
                        queue_by_time=queue_counts_by_type[bucket].get(zone_id, {}).get(crowd_type, {}),
                        total_entries=entries_by_type[bucket].get(zone_id, {}).get(crowd_type, 0),
                        total_exits=exits_by_type[bucket].get(zone_id, {}).get(crowd_type, 0),
                        stay_values=stay_values_by_type[bucket].get(zone_id, {}).get(crowd_type, []),
                        capacity=zone_capacity.get(zone_id),
                    )
                )

    return rows


def _build_single_zone_metric_row(
    *,
    experiment_pk: int,
    bucket_start: int,
    zone_id: str,
    crowd_type: str | None,
    counts_by_time: dict[int, int],
    queue_by_time: dict[int, int],
    total_entries: int,
    total_exits: int,
    stay_values: list[int],
    capacity: int | None,
) -> ZoneMetric1m:
    """构造单个区域聚合行，并把利用率限制在 0~1。"""

    peak_count = max(counts_by_time.values(), default=0)
    return ZoneMetric1m(
        experiment_id=experiment_pk,
        bucket_start_ms=bucket_start,
        zone_id=zone_id,
        crowd_type=crowd_type,
        sample_count=sum(counts_by_time.values()),
        avg_count=_round_average(list(counts_by_time.values()), digits=2),
        peak_count=peak_count,
        total_entries=total_entries,
        total_exits=total_exits,
        queue_peak_count=max(queue_by_time.values(), default=0),
        avg_stay_ms=_round_average_to_int(stay_values),
        # 聚合层统一把区域利用率限制在 0~1，便于分析接口和驾驶舱直接消费。
        utilization_rate=_safe_utilization_rate(peak_count, capacity),
    )


def _build_exit_metric_rows(
    *,
    experiment_pk: int,
    bucket_starts: list[int],
    exits: list[ExperimentExit],
    points: list[TrajectoryPoint],
    decisions: list[AgentDecision],
) -> list[ExitMetric1m]:
    """构造出口分钟聚合行。

    出口排队长度来自轨迹点的 target_exit_id + is_queuing；通行次数优先使用轨迹点携带的
    target_exit_id，如果缺失则通过 source_zone -> target_zone 拓扑关系反推。
    """

    rows: list[ExitMetric1m] = []
    bucket_set = set(bucket_starts)
    exit_by_pair = {(item.source_zone_id, item.target_zone_id): item.exit_id for item in exits}

    queue_counts: dict[int, dict[str, dict[int, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    queue_counts_by_type: dict[int, dict[str, dict[str, dict[int, int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(int)))
    )
    wait_values: dict[int, dict[str, list[int]]] = defaultdict(lambda: defaultdict(list))
    wait_values_by_type: dict[int, dict[str, dict[str, list[int]]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    passages: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    passages_by_type: dict[int, dict[str, dict[str, int]]] = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

    for point in points:
        point_bucket = bucket_start_ms(int(point.simulation_time_ms))
        if point_bucket not in bucket_set:
            continue
        if point.target_exit_id is not None and point.is_queuing:
            simulation_time_ms = int(point.simulation_time_ms)
            queue_counts[point_bucket][point.target_exit_id][simulation_time_ms] += 1
            queue_counts_by_type[point_bucket][point.target_exit_id][point.crowd_type][simulation_time_ms] += 1

    for decision in decisions:
        if decision.chosen_exit_id is None:
            continue
        decision_bucket = bucket_start_ms(int(decision.simulation_time_ms))
        wait_values[decision_bucket][decision.chosen_exit_id].append(int(decision.stay_duration_ms))
        wait_values_by_type[decision_bucket][decision.chosen_exit_id][decision.crowd_type].append(
            int(decision.stay_duration_ms)
        )

    for agent_points in _group_points_by_agent(points).values():
        for previous, current in zip(agent_points, agent_points[1:]):
            current_bucket = bucket_start_ms(int(current.simulation_time_ms))
            if current_bucket not in bucket_set or previous.zone_id == current.zone_id:
                continue

            exit_id = previous.target_exit_id or exit_by_pair.get((previous.zone_id, current.zone_id))
            if exit_id is None:
                continue

            passages[current_bucket][exit_id] += 1
            passages_by_type[current_bucket][exit_id][current.crowd_type] += 1

    for bucket in bucket_starts:
        candidate_exit_ids = set(passages[bucket]) | set(queue_counts[bucket]) | set(wait_values[bucket])
        for exit_id in sorted(candidate_exit_ids):
            rows.append(
                _build_single_exit_metric_row(
                    experiment_pk=experiment_pk,
                    bucket_start=bucket,
                    exit_id=exit_id,
                    crowd_type=None,
                    total_passages=passages[bucket].get(exit_id, 0),
                    queue_by_time=queue_counts[bucket].get(exit_id, {}),
                    wait_values=wait_values[bucket].get(exit_id, []),
                )
            )

            crowd_type_keys = (
                set(passages_by_type[bucket].get(exit_id, {}))
                | set(queue_counts_by_type[bucket].get(exit_id, {}))
                | set(wait_values_by_type[bucket].get(exit_id, {}))
            )
            for crowd_type in sorted(crowd_type_keys):
                rows.append(
                    _build_single_exit_metric_row(
                        experiment_pk=experiment_pk,
                        bucket_start=bucket,
                        exit_id=exit_id,
                        crowd_type=crowd_type,
                        total_passages=passages_by_type[bucket].get(exit_id, {}).get(crowd_type, 0),
                        queue_by_time=queue_counts_by_type[bucket].get(exit_id, {}).get(crowd_type, {}),
                        wait_values=wait_values_by_type[bucket].get(exit_id, {}).get(crowd_type, []),
                    )
                )

    return rows


def _build_single_exit_metric_row(
    *,
    experiment_pk: int,
    bucket_start: int,
    exit_id: str,
    crowd_type: str | None,
    total_passages: int,
    queue_by_time: dict[int, int],
    wait_values: list[int],
) -> ExitMetric1m:
    """构造单个出口聚合行。"""

    return ExitMetric1m(
        experiment_id=experiment_pk,
        bucket_start_ms=bucket_start,
        exit_id=exit_id,
        crowd_type=crowd_type,
        total_passages=total_passages,
        avg_queue_length=_round_average(list(queue_by_time.values()), digits=2),
        max_queue_length=max(queue_by_time.values(), default=0),
        avg_wait_ms=_round_average_to_int(wait_values),
        max_wait_ms=max(wait_values, default=0),
        throughput_per_min=round(float(total_passages), 2),
    )


def _group_points_by_agent(points: list[TrajectoryPoint]) -> dict[str, list[TrajectoryPoint]]:
    """按 agent_id 分组轨迹点，保留传入列表中的时间排序。"""

    grouped: dict[str, list[TrajectoryPoint]] = defaultdict(list)
    for point in points:
        grouped[point.agent_id].append(point)
    return grouped


def _list_last_sampled_points_before_time(
    session: Session,
    *,
    experiment_pk: int,
    agent_ids: list[str],
    time_ms: int,
) -> list[TrajectoryPointSampled1s]:
    """读取每个 agent 在刷新窗口之前的最后一个采样点，作为增量采样 seed。"""

    if not agent_ids:
        return []

    latest_per_agent = (
        select(
            TrajectoryPointSampled1s.agent_id.label("agent_id"),
            func.max(TrajectoryPointSampled1s.simulation_time_ms).label("simulation_time_ms"),
        )
        .where(
            TrajectoryPointSampled1s.experiment_id == experiment_pk,
            TrajectoryPointSampled1s.agent_id.in_(agent_ids),
            TrajectoryPointSampled1s.simulation_time_ms < time_ms,
        )
        .group_by(TrajectoryPointSampled1s.agent_id)
        .subquery()
    )
    stmt = (
        select(TrajectoryPointSampled1s)
        .join(
            latest_per_agent,
            (TrajectoryPointSampled1s.agent_id == latest_per_agent.c.agent_id)
            & (TrajectoryPointSampled1s.simulation_time_ms == latest_per_agent.c.simulation_time_ms),
        )
        .where(TrajectoryPointSampled1s.experiment_id == experiment_pk)
        .order_by(TrajectoryPointSampled1s.agent_id)
    )
    return list(session.scalars(stmt))


def _list_points_for_agents_from_time(
    session: Session,
    *,
    experiment_pk: int,
    agent_ids: list[str],
    time_ms: int,
) -> list[TrajectoryPoint]:
    """读取指定 agent 从某个时间点开始的原始轨迹点。"""

    if not agent_ids:
        return []

    stmt = (
        select(TrajectoryPoint)
        .where(
            TrajectoryPoint.experiment_id == experiment_pk,
            TrajectoryPoint.agent_id.in_(agent_ids),
            TrajectoryPoint.simulation_time_ms >= time_ms,
        )
        .order_by(TrajectoryPoint.agent_id, TrajectoryPoint.simulation_time_ms)
    )
    return list(session.scalars(stmt))


def _build_sampled_trajectory_rows(
    *,
    experiment_pk: int,
    points: list[TrajectoryPoint],
    sample_interval_ms: int,
    seed_by_agent: dict[str, TrajectoryPointSampled1s],
) -> list[TrajectoryPointSampled1s]:
    """从原始轨迹点生成 1 秒采样轨迹。

    除了固定时间间隔外，只要 zone/state/queue/target_exit 发生变化也会保留该点，
    避免 compact 回放丢失关键状态切换。
    """

    rows: list[TrajectoryPointSampled1s] = []
    last_kept_by_agent: dict[str, object] = dict(seed_by_agent)

    for point in points:
        last_kept = last_kept_by_agent.get(point.agent_id)
        if not _should_keep_sample_point(last_kept=last_kept, current=point, sample_interval_ms=sample_interval_ms):
            continue

        row = TrajectoryPointSampled1s(
            experiment_id=experiment_pk,
            agent_id=point.agent_id,
            simulation_time_ms=int(point.simulation_time_ms),
            crowd_type=point.crowd_type,
            zone_id=point.zone_id,
            position_x=round_coordinate(point.position_x),
            position_z=round_coordinate(point.position_z),
            speed=point.speed,
            state=point.state,
            target_exit_id=point.target_exit_id,
            is_queuing=point.is_queuing,
            sample_interval_ms=point.sample_interval_ms,
            source_batch_id=point.batch_id,
            ingested_at=point.ingested_at,
        )
        rows.append(row)
        last_kept_by_agent[point.agent_id] = row

    return rows


def _should_keep_sample_point(*, last_kept: object | None, current: TrajectoryPoint, sample_interval_ms: int) -> bool:
    """判断当前轨迹点是否应该进入采样表。"""

    if last_kept is None:
        return True

    last_time_ms = int(getattr(last_kept, "simulation_time_ms"))
    if int(current.simulation_time_ms) - last_time_ms >= sample_interval_ms:
        return True
    if getattr(last_kept, "zone_id") != current.zone_id:
        return True
    if getattr(last_kept, "state") != current.state:
        return True
    if bool(getattr(last_kept, "is_queuing")) != bool(current.is_queuing):
        return True
    if getattr(last_kept, "target_exit_id") != current.target_exit_id:
        return True
    return False


def _round_average(values: list[int | float], *, digits: int) -> float:
    """计算平均值并按指定小数位四舍五入；空列表返回 0。"""

    if not values:
        return 0.0
    return round(sum(float(value) for value in values) / len(values), digits)


def _round_average_to_int(values: list[int]) -> int | None:
    """计算整数平均值；空列表返回 None，表示该指标没有样本。"""

    if not values:
        return None
    return int(round(sum(values) / len(values)))


def _safe_ratio(numerator: int | float, denominator: int | float | None) -> float | None:
    """安全计算比例，分母为空或 0 时返回 None。"""

    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 4)


def _safe_utilization_rate(numerator: int | float, denominator: int | float | None) -> float | None:
    """区域利用率对外只保留 0~1；超过容量时由峰值人数表达超载。"""

    ratio = _safe_ratio(numerator, denominator)
    if ratio is None:
        return None
    return min(ratio, 1.0)
