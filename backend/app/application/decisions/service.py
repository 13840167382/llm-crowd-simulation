"""决策批次应用服务。

Unity 在运行中的实验里按批次提交人物决策请求，本服务负责把这些请求落库、
调用真实/模拟决策引擎，并在引擎不可用、超时或返回异常时按实验配置降级到算法推荐。
这里同时承担幂等、防重入和分钟聚合刷新，因此是运行时主链路中最需要保持一致性的服务之一。
"""

from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from datetime import datetime, timezone
from threading import current_thread
from time import perf_counter
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.application.analytics.aggregation import (
    refresh_agent_metrics_for_times,
    refresh_decision_metrics_for_times,
)
from backend.app.application.common import (
    datetime_to_timestamp_ms,
    decimal_to_float,
    raise_batch_size_exceeded,
    raise_experiment_status_conflict,
    raise_invalid_field_value,
    raise_llm_service_unavailable,
)
from backend.app.application.decisions.engine import (
    DecisionEngine,
    DecisionEngineCallFailed,
    DecisionEngineContext,
    DecisionEngineResponseParseFailed,
    DecisionEngineUnavailable,
    get_decision_engine,
)
from backend.app.application.experiments.service import get_experiment_or_404
from backend.app.core.config import get_settings
from backend.app.domain.enums import DecisionBatchStatus, DecisionSource, ExperimentStatus
from backend.app.infrastructure.cache import get_redis_client
from backend.app.infrastructure.db.models import AgentDecision, CrowdTypeTemplate, DecisionBatch
from backend.app.infrastructure.db.repositories import AgentDecisionRepository, DecisionBatchRepository
from backend.app.infrastructure.observability.logger import get_logger
from backend.app.schemas.decisions import (
    AvailableExitOption,
    DecisionAgentRequest,
    DecisionBatchRequest,
    DecisionBatchResponseData,
    DecisionResultItem,
)

logger = get_logger(__name__)


def create_decision_batch(
    session: Session,
    *,
    experiment_id: str,
    payload: DecisionBatchRequest,
    request_id: str | None = None,
) -> DecisionBatchResponseData:
    """创建一个决策批次，并按 LLM/fallback 语义返回。

    主流程分成五段：
    1. 校验实验必须处于 RUNNING，避免对已结束或未启动实验写入运行数据。
    2. 通过数据库幂等和 Redis 锁拦截重复 batch_id 或同实验并发请求。
    3. 解析决策引擎，如果模型服务不可用且允许 fallback，则整批走算法推荐。
    4. 按配置串行或有限并发处理每个人物，保证响应顺序仍与输入 agents 一致。
    5. 持久化批次、明细和聚合指标，最后释放 Redis 锁。
    """

    experiment = get_experiment_or_404(session, experiment_id)
    if experiment.status != ExperimentStatus.RUNNING:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前状态 `{experiment.status}` 不允许提交决策批次",
            data={"status": experiment.status},
        )

    batch_repository = DecisionBatchRepository(session)
    decision_repository = AgentDecisionRepository(session)

    # 第一层幂等：数据库里已经有同一实验 + batch_id 的批次时，直接复用历史响应。
    existing_batch = batch_repository.get_by_experiment_and_batch_id(experiment.id, payload.batch_id)
    if existing_batch is not None:
        logger.info("决策批次命中幂等缓存: experiment_id=%s batch_id=%s", experiment.experiment_id, payload.batch_id)
        return _resolve_existing_decision_batch(experiment_id=experiment.experiment_id, batch=existing_batch)

    # 第二层幂等：Redis 批次锁用于挡住同一个 batch_id 的并发重复提交。
    batch_lock_enabled, batch_lock_token = _acquire_decision_batch_lock(
        experiment_pk=experiment.id,
        batch_id=payload.batch_id,
    )
    if batch_lock_enabled and batch_lock_token is None:
        existing_batch = batch_repository.get_by_experiment_and_batch_id(experiment.id, payload.batch_id)
        if existing_batch is not None:
            logger.info(
                "决策批次在等待期间已完成，直接返回缓存: experiment_id=%s batch_id=%s",
                experiment.experiment_id,
                payload.batch_id,
            )
            return _resolve_existing_decision_batch(experiment_id=experiment.experiment_id, batch=existing_batch)

        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 的决策批次 `{payload.batch_id}` 正在处理中",
            data={"batch_id": payload.batch_id, "reason": "decision_batch_processing"},
        )

    # 实验级锁限制同一实验同一时刻只有一个决策批次在跑，避免外部模型被瞬时打满。
    experiment_lock_enabled, experiment_lock_token = _acquire_experiment_decision_lock(
        experiment_pk=experiment.id,
    )
    if experiment_lock_enabled and experiment_lock_token is None:
        raise_experiment_status_conflict(
            f"实验 `{experiment.experiment_id}` 当前已有未完成的决策请求",
            data={"batch_id": payload.batch_id, "reason": "decision_request_inflight"},
        )

    try:
        # 拿到锁后再查一次，处理“等待锁期间另一个请求已写完数据库”的竞态。
        existing_batch = batch_repository.get_by_experiment_and_batch_id(experiment.id, payload.batch_id)
        if existing_batch is not None:
            logger.info("决策批次二次检查命中缓存: experiment_id=%s batch_id=%s", experiment.experiment_id, payload.batch_id)
            return _resolve_existing_decision_batch(experiment_id=experiment.experiment_id, batch=existing_batch)

        _validate_batch_size(payload, limit=experiment.decision_batch_size)
        _validate_duplicate_agents(payload)

        engine = _resolve_decision_engine_or_raise(
            fallback_to_algorithm=bool(experiment.fallback_to_algorithm),
            experiment_id=experiment.experiment_id,
            batch_id=payload.batch_id,
        )
        settings = get_settings()
        batch_hard_timeout_ms = max(int(settings.decision_batch_hard_timeout_seconds * 1000), 0)
        crowd_type_display_names = _load_crowd_type_display_names(session, payload)

        started_at = perf_counter()
        processed_at = datetime.now(timezone.utc)

        # 先写 PROCESSING 批次记录，再调用 LLM。这样重复请求能看到处理中状态，而不是继续并发执行。
        batch = DecisionBatch(
            experiment_id=experiment.id,
            batch_id=payload.batch_id,
            simulation_time_ms=payload.simulation_time_ms,
            requested_count=len(payload.agents),
            success_count=0,
            fallback_count=0,
            processing_duration_ms=0,
            status=DecisionBatchStatus.PROCESSING.value,
            response_payload={},
            processed_at=processed_at,
        )
        inserted_batch = batch_repository.add_if_absent(batch)
        if inserted_batch is None:
            existing_batch = batch_repository.get_by_experiment_and_batch_id(experiment.id, payload.batch_id)
            if existing_batch is not None:
                logger.info(
                    "决策批次数据库幂等命中: experiment_id=%s batch_id=%s",
                    experiment.experiment_id,
                    payload.batch_id,
                )
                return _resolve_existing_decision_batch(experiment_id=experiment.experiment_id, batch=existing_batch)
            raise_experiment_status_conflict(
                f"实验 `{experiment.experiment_id}` 的决策批次 `{payload.batch_id}` 正在处理中",
                data={"batch_id": payload.batch_id, "reason": "decision_batch_processing"},
            )
        batch = inserted_batch

        result_items: list[DecisionResultItem] = []
        decision_rows: list[AgentDecision] = []
        batch_results, batch_timeout_triggered = _build_batch_decision_results(
            payload=payload,
            engine=engine,
            started_at=started_at,
            experiment_id=experiment.experiment_id,
            request_id=request_id,
            crowd_type_display_names=crowd_type_display_names,
            llm_model=experiment.llm_model,
            llm_timeout_ms=int(experiment.llm_timeout_ms),
            fallback_to_algorithm=bool(experiment.fallback_to_algorithm),
            batch_hard_timeout_ms=batch_hard_timeout_ms,
        )

        # batch_results 已按输入 agents 顺序排好，这里一一写入明细，方便后续统计 override/fallback。
        for agent, (result_item, llm_latency_ms, was_overridden) in zip(payload.agents, batch_results, strict=True):
            result_items.append(result_item)
            decision_rows.append(
                AgentDecision(
                    experiment_id=experiment.id,
                    decision_batch_id=batch.id,
                    agent_id=agent.agent_id,
                    simulation_time_ms=payload.simulation_time_ms,
                    crowd_type=agent.crowd_type,
                    current_zone_id=agent.current_zone_id,
                    stay_duration_ms=agent.stay_duration_ms,
                    patience_ratio=agent.patience_ratio,
                    algorithm_recommendation=agent.algorithm_recommendation,
                    available_exits_json=[item.model_dump(mode="json") for item in agent.available_exits],
                    dilemma_summary=agent.dilemma_summary,
                    chosen_exit_id=result_item.chosen_exit_id,
                    decision_source=result_item.decision_source,
                    reasoning=result_item.reasoning,
                    confidence=result_item.confidence,
                    was_overridden=was_overridden,
                    llm_latency_ms=llm_latency_ms,
                )
            )

        decision_repository.add_all(decision_rows)
        session.flush()

        llm_success_count = sum(1 for item in result_items if item.decision_source == DecisionSource.LLM)
        fallback_count = sum(1 for item in result_items if item.decision_source == DecisionSource.FALLBACK)
        resolved_count = llm_success_count + fallback_count
        processing_duration_ms = max(0, int((perf_counter() - started_at) * 1000))

        response_data = DecisionBatchResponseData(
            batch_id=payload.batch_id,
            processed_at_ms=datetime_to_timestamp_ms(processed_at),
            processing_duration_ms=processing_duration_ms,
            total_count=len(result_items),
            success_count=llm_success_count,
            fallback_count=fallback_count,
            decisions=result_items,
        )

        batch.success_count = llm_success_count
        batch.fallback_count = fallback_count
        batch.processing_duration_ms = processing_duration_ms
        batch.status = _resolve_batch_status(total_count=len(result_items), resolved_count=resolved_count).value
        batch.response_payload = response_data.model_dump(mode="json")

        # 决策批次是运行期数据入口，提交成功后立即刷新相关分钟聚合，保证分析接口能读到新值。
        refresh_decision_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=[payload.simulation_time_ms],
        )
        refresh_agent_metrics_for_times(
            session,
            experiment_pk=experiment.id,
            simulation_time_ms_values=[payload.simulation_time_ms],
        )
        session.commit()

        logger.info(
            "决策批次处理完成: experiment_id=%s batch_id=%s total=%s llm_success=%s fallback=%s hard_timeout=%s",
            experiment.experiment_id,
            payload.batch_id,
            len(result_items),
            llm_success_count,
            fallback_count,
            batch_timeout_triggered,
        )
        return response_data
    finally:
        _release_experiment_decision_lock(
            experiment_pk=experiment.id,
            lock_enabled=experiment_lock_enabled,
            lock_token=experiment_lock_token,
        )
        _release_decision_batch_lock(
            experiment_pk=experiment.id,
            batch_id=payload.batch_id,
            lock_enabled=batch_lock_enabled,
            lock_token=batch_lock_token,
        )


def _build_batch_decision_results(
    *,
    payload: DecisionBatchRequest,
    engine: DecisionEngine | None,
    started_at: float,
    experiment_id: str,
    request_id: str | None,
    crowd_type_display_names: dict[str, str],
    llm_model: str,
    llm_timeout_ms: int,
    fallback_to_algorithm: bool,
    batch_hard_timeout_ms: int,
) -> tuple[list[tuple[DecisionResultItem, int | None, bool]], bool]:
    """按原始输入顺序构建整批决策结果，并在允许时做有限并发。

    外层调用者依赖返回结果和 payload.agents 的顺序严格一致，所以并发分支内部会用
    agent 的原始 index 回填结果。即使部分任务超时或异常，也会填入 fallback/none 结果，
    避免 zip 写库时丢失人物。
    """

    contexts = [
        DecisionEngineContext(
            experiment_id=experiment_id,
            batch_id=payload.batch_id,
            request_id=request_id,
            simulation_time_ms=payload.simulation_time_ms,
            crowd_type_display_name=crowd_type_display_names.get(agent.crowd_type, agent.crowd_type),
        )
        for agent in payload.agents
    ]
    max_concurrency = _resolve_decision_batch_max_concurrency(len(payload.agents))
    logger.info(
        "决策批次开始构建人物决策结果: experiment_id=%s batch_id=%s agent_count=%s max_concurrency=%s hard_timeout_ms=%s",
        experiment_id,
        payload.batch_id,
        len(payload.agents),
        max_concurrency,
        batch_hard_timeout_ms,
    )

    if max_concurrency <= 1:
        return _build_batch_decision_results_sequentially(
            payload=payload,
            engine=engine,
            started_at=started_at,
            contexts=contexts,
            llm_model=llm_model,
            llm_timeout_ms=llm_timeout_ms,
            fallback_to_algorithm=fallback_to_algorithm,
            batch_hard_timeout_ms=batch_hard_timeout_ms,
        )

    return _build_batch_decision_results_concurrently(
        payload=payload,
        engine=engine,
        started_at=started_at,
        contexts=contexts,
        llm_model=llm_model,
        llm_timeout_ms=llm_timeout_ms,
        fallback_to_algorithm=fallback_to_algorithm,
        batch_hard_timeout_ms=batch_hard_timeout_ms,
        max_concurrency=max_concurrency,
    )


def _build_batch_decision_results_sequentially(
    *,
    payload: DecisionBatchRequest,
    engine: DecisionEngine | None,
    started_at: float,
    contexts: list[DecisionEngineContext],
    llm_model: str,
    llm_timeout_ms: int,
    fallback_to_algorithm: bool,
    batch_hard_timeout_ms: int,
) -> tuple[list[tuple[DecisionResultItem, int | None, bool]], bool]:
    """串行执行人物决策，作为最保守的回退模式。

    当并发上限为 1、决策引擎不可用或需要降低外部模型压力时使用。硬超时触发后，
    后续人物不再调用引擎，统一构造 timeout fallback/none 结果。
    """

    results: list[tuple[DecisionResultItem, int | None, bool]] = []
    batch_timeout_triggered = False
    total_agents = len(payload.agents)

    for index, agent in enumerate(payload.agents):
        logger.info(
            "人物进入串行决策处理: batch_id=%s agent_id=%s progress=%s/%s",
            payload.batch_id,
            agent.agent_id,
            index + 1,
            total_agents,
        )
        if _is_batch_hard_timeout_reached(started_at, batch_hard_timeout_ms):
            batch_timeout_triggered = True
            result = _build_timeout_result(
                agent,
                fallback_to_algorithm=fallback_to_algorithm,
            )
            results.append(result)
            logger.info(
                "人物串行决策结束: batch_id=%s agent_id=%s source=%s chosen_exit_id=%s progress=%s/%s",
                payload.batch_id,
                agent.agent_id,
                result[0].decision_source,
                result[0].chosen_exit_id,
                len(results),
                total_agents,
            )
            continue

        result = _build_agent_decision_result(
            agent,
            engine=engine,
            context=contexts[index],
            llm_model=llm_model,
            llm_timeout_ms=llm_timeout_ms,
            fallback_to_algorithm=fallback_to_algorithm,
        )
        results.append(result)
        logger.info(
            "人物串行决策结束: batch_id=%s agent_id=%s source=%s chosen_exit_id=%s progress=%s/%s",
            payload.batch_id,
            agent.agent_id,
            result[0].decision_source,
            result[0].chosen_exit_id,
            len(results),
            total_agents,
        )

    return results, batch_timeout_triggered


def _build_batch_decision_results_concurrently(
    *,
    payload: DecisionBatchRequest,
    engine: DecisionEngine | None,
    started_at: float,
    contexts: list[DecisionEngineContext],
    llm_model: str,
    llm_timeout_ms: int,
    fallback_to_algorithm: bool,
    batch_hard_timeout_ms: int,
    max_concurrency: int,
) -> tuple[list[tuple[DecisionResultItem, int | None, bool]], bool]:
    """有限并发执行人物决策，提升吞吐同时避免无上限打满模型服务。

    ThreadPool 只负责把人物级调用并发化；批次级硬超时仍由主线程控制。
    超时后会取消尚未开始的 future，并把未完成人物补成 timeout fallback/none。
    Python 线程无法强杀已进入外部调用的任务，所以这里用 wait=False 收尾，并依赖单人
    LLM 超时和 Redis 锁 TTL 防止请求长时间阻塞主流程。
    """

    if engine is None:
        return _build_batch_decision_results_sequentially(
            payload=payload,
            engine=engine,
            started_at=started_at,
            contexts=contexts,
            llm_model=llm_model,
            llm_timeout_ms=llm_timeout_ms,
            fallback_to_algorithm=fallback_to_algorithm,
            batch_hard_timeout_ms=batch_hard_timeout_ms,
        )

    batch_timeout_triggered = False
    total_agents = len(payload.agents)
    completed_count = 0
    results: list[tuple[DecisionResultItem, int | None, bool] | None] = [None] * len(payload.agents)
    executor = ThreadPoolExecutor(max_workers=max_concurrency, thread_name_prefix="decision-batch")
    future_to_index: dict[Future[tuple[DecisionResultItem, int | None, bool]], int] = {}

    try:
        for index, agent in enumerate(payload.agents):
            future = executor.submit(
                _build_agent_decision_result,
                agent,
                engine=engine,
                context=contexts[index],
                llm_model=llm_model,
                llm_timeout_ms=llm_timeout_ms,
                fallback_to_algorithm=fallback_to_algorithm,
            )
            future_to_index[future] = index
            logger.info(
                "人物已提交到并发决策池: batch_id=%s agent_id=%s submitted=%s/%s max_concurrency=%s",
                payload.batch_id,
                agent.agent_id,
                index + 1,
                total_agents,
                max_concurrency,
            )

        pending: set[Future[tuple[DecisionResultItem, int | None, bool]]] = set(future_to_index)
        while pending:
            remaining_timeout_seconds = _calculate_remaining_batch_timeout_seconds(
                started_at=started_at,
                hard_timeout_ms=batch_hard_timeout_ms,
            )
            if remaining_timeout_seconds is not None and remaining_timeout_seconds <= 0:
                batch_timeout_triggered = True
                logger.warning(
                    "决策批次达到硬超时阈值，准备中止剩余人物处理: batch_id=%s completed=%s/%s pending=%s hard_timeout_ms=%s",
                    payload.batch_id,
                    completed_count,
                    total_agents,
                    len(pending),
                    batch_hard_timeout_ms,
                )
                break

            done, pending = wait(
                pending,
                timeout=remaining_timeout_seconds,
                return_when=FIRST_COMPLETED,
            )
            if not done:
                batch_timeout_triggered = bool(pending)
                logger.warning(
                    "决策批次等待并发结果超时，剩余人物将统一回退: batch_id=%s completed=%s/%s pending=%s hard_timeout_ms=%s",
                    payload.batch_id,
                    completed_count,
                    total_agents,
                    len(pending),
                    batch_hard_timeout_ms,
                )
                break

            for future in done:
                agent_index = future_to_index[future]
                agent = payload.agents[agent_index]
                try:
                    results[agent_index] = future.result()
                except Exception as exc:  # noqa: BLE001
                    logger.exception(
                        "并行人物决策发生未捕获异常，已按 fallback/none 处理: agent_id=%s error=%s",
                        agent.agent_id,
                        exc,
                    )
                    results[agent_index] = _build_failed_result(
                        agent,
                        failure_reason=f"unexpected_engine_error: {exc}",
                        fallback_to_algorithm=fallback_to_algorithm,
                        llm_latency_ms=None,
                    )
                completed_count += 1
                result_item = results[agent_index][0]
                logger.info(
                    "人物并发决策结束: batch_id=%s agent_id=%s source=%s chosen_exit_id=%s completed=%s/%s pending=%s",
                    payload.batch_id,
                    agent.agent_id,
                    result_item.decision_source,
                    result_item.chosen_exit_id,
                    completed_count,
                    total_agents,
                    len(pending),
                )

        if pending:
            for future in pending:
                future.cancel()
                agent_index = future_to_index[future]
                if results[agent_index] is None:
                    results[agent_index] = _build_timeout_result(
                        payload.agents[agent_index],
                        fallback_to_algorithm=fallback_to_algorithm,
                    )
                logger.warning(
                    "人物并发决策被批次硬超时收尾: batch_id=%s agent_id=%s completed=%s/%s",
                    payload.batch_id,
                    payload.agents[agent_index].agent_id,
                    completed_count,
                    total_agents,
                )
    finally:
        executor.shutdown(wait=False, cancel_futures=True)

    finalized_results = [
        result
        if result is not None
        else _build_timeout_result(
            payload.agents[index],
            fallback_to_algorithm=fallback_to_algorithm,
        )
        for index, result in enumerate(results)
    ]
    logger.info(
        "决策批次并发阶段结束: batch_id=%s total=%s completed=%s hard_timeout=%s",
        payload.batch_id,
        total_agents,
        completed_count,
        batch_timeout_triggered,
    )
    return finalized_results, batch_timeout_triggered


def _resolve_decision_engine_or_raise(
    *,
    fallback_to_algorithm: bool,
    experiment_id: str,
    batch_id: str,
) -> DecisionEngine | None:
    """解析决策引擎，不可用时按实验配置决定失败还是整批 fallback。

    返回 None 表示允许 fallback，后续每个人物都会直接构造算法推荐结果；
    抛出业务错误则让 API 明确告诉 Unity 当前模型服务不可用。
    """

    try:
        return get_decision_engine()
    except DecisionEngineUnavailable as exc:
        if fallback_to_algorithm:
            logger.warning(
                "决策引擎不可用，当前批次将统一降级为 fallback: experiment_id=%s batch_id=%s error=%s",
                experiment_id,
                batch_id,
                exc,
            )
            return None

        raise_llm_service_unavailable(
            f"实验 `{experiment_id}` 的决策引擎当前不可用",
            data={"batch_id": batch_id, "reason": "llm_service_unavailable"},
        )


def _resolve_existing_decision_batch(*, experiment_id: str, batch: DecisionBatch) -> DecisionBatchResponseData:
    """把已存在的批次记录还原为 API 响应，处理中批次返回冲突错误。"""

    if batch.response_payload:
        return DecisionBatchResponseData.model_validate(batch.response_payload)

    raise_experiment_status_conflict(
        f"实验 `{experiment_id}` 的决策批次 `{batch.batch_id}` 正在处理中",
        data={"batch_id": batch.batch_id, "reason": "decision_batch_processing"},
    )


def _validate_batch_size(payload: DecisionBatchRequest, *, limit: int) -> None:
    """校验单批人物数量不超过实验配置的批次上限。"""

    if len(payload.agents) <= limit:
        return

    raise_batch_size_exceeded(
        f"决策批次 `{payload.batch_id}` 的 agent 数量超过实验配置上限",
        data={"limit": limit, "requested_count": len(payload.agents)},
    )


def _validate_duplicate_agents(payload: DecisionBatchRequest) -> None:
    """禁止同一个批次里重复提交相同 agent_id，避免明细和统计出现双计数。"""

    seen_agent_ids: set[str] = set()
    duplicate_agent_ids: list[str] = []

    for agent in payload.agents:
        if agent.agent_id in seen_agent_ids:
            duplicate_agent_ids.append(agent.agent_id)
            continue
        seen_agent_ids.add(agent.agent_id)

    if not duplicate_agent_ids:
        return

    raise_invalid_field_value(
        f"决策批次 `{payload.batch_id}` 中存在重复的 agent_id",
        data={"duplicate_agent_ids": sorted(set(duplicate_agent_ids))},
    )


def _load_crowd_type_display_names(
    session: Session,
    payload: DecisionBatchRequest,
) -> dict[str, str]:
    """为提示词上下文补齐人群类型展示名，查不到时由调用方回退到 type_id。"""

    type_ids = sorted({agent.crowd_type for agent in payload.agents})
    if not type_ids:
        return {}

    rows = session.execute(
        select(CrowdTypeTemplate.type_id, CrowdTypeTemplate.display_name).where(
            CrowdTypeTemplate.type_id.in_(type_ids)
        )
    ).all()
    return {type_id: display_name for type_id, display_name in rows}


def _build_agent_decision_result(
    agent: DecisionAgentRequest,
    *,
    engine: DecisionEngine | None,
    context: DecisionEngineContext,
    llm_model: str,
    llm_timeout_ms: int,
    fallback_to_algorithm: bool,
) -> tuple[DecisionResultItem, int | None, bool]:
    """执行单个人物决策，并把模型异常统一转换成可落库的结果。

    成功路径会校验 LLM 返回的 chosen_exit_id 必须来自候选出口列表；失败路径根据
    fallback_to_algorithm 决定返回算法推荐还是空决策，避免异常穿透到整批处理。
    """

    if engine is None:
        return _build_failed_result(
            agent,
            failure_reason="llm_service_unavailable",
            fallback_to_algorithm=fallback_to_algorithm,
            llm_latency_ms=None,
        )

    logger.info(
        "人物进入决策引擎: experiment_id=%s batch_id=%s agent_id=%s worker=%s zone_id=%s available_exit_count=%s",
        context.experiment_id,
        context.batch_id,
        agent.agent_id,
        current_thread().name,
        agent.current_zone_id,
        len(agent.available_exits),
    )
    started_at = perf_counter()
    try:
        engine_result = engine.decide(
            agent,
            context=context,
            llm_timeout_ms=llm_timeout_ms,
            llm_model=llm_model,
        )
        llm_latency_ms = max(1, int((perf_counter() - started_at) * 1000))
        if llm_timeout_ms > 0 and llm_latency_ms > llm_timeout_ms:
            raise DecisionEngineCallFailed(
                f"人物 `{agent.agent_id}` 的 LLM 决策耗时 {llm_latency_ms}ms，超过阈值 {llm_timeout_ms}ms"
            )

        if not _exit_exists(agent.available_exits, engine_result.chosen_exit_id):
            raise DecisionEngineResponseParseFailed(
                f"人物 `{agent.agent_id}` 的 LLM 返回了不在候选出口中的 exit_id `{engine_result.chosen_exit_id}`"
            )

        chosen_exit_id = engine_result.chosen_exit_id
        was_overridden = _was_algorithm_overridden(agent, chosen_exit_id)
        logger.info(
            "人物决策成功返回: experiment_id=%s batch_id=%s agent_id=%s worker=%s source=%s chosen_exit_id=%s llm_latency_ms=%s overridden=%s",
            context.experiment_id,
            context.batch_id,
            agent.agent_id,
            current_thread().name,
            DecisionSource.LLM,
            chosen_exit_id,
            llm_latency_ms,
            was_overridden,
        )
        return (
            DecisionResultItem(
                agent_id=agent.agent_id,
                chosen_exit_id=chosen_exit_id,
                decision_source=DecisionSource.LLM,
                reasoning=engine_result.reasoning,
                confidence=_normalize_confidence(engine_result.confidence),
            ),
            llm_latency_ms,
            was_overridden,
        )
    except (DecisionEngineCallFailed, DecisionEngineResponseParseFailed, DecisionEngineUnavailable) as exc:
        llm_latency_ms = max(1, int((perf_counter() - started_at) * 1000))
        logger.warning(
            "人物决策改走 fallback/none: agent_id=%s reason=%s llm_latency_ms=%s",
            agent.agent_id,
            exc,
            llm_latency_ms,
        )
        return _build_failed_result(
            agent,
            failure_reason=str(exc),
            fallback_to_algorithm=fallback_to_algorithm,
            llm_latency_ms=llm_latency_ms,
        )


def _build_timeout_result(
    agent: DecisionAgentRequest,
    *,
    fallback_to_algorithm: bool,
) -> tuple[DecisionResultItem, int | None, bool]:
    """批次硬超时后的统一结果构造入口。"""

    logger.warning("决策批次硬超时，剩余人物统一走 fallback/none: agent_id=%s", agent.agent_id)
    return _build_failed_result(
        agent,
        failure_reason="llm_batch_timeout",
        fallback_to_algorithm=fallback_to_algorithm,
        llm_latency_ms=None,
    )


def _build_failed_result(
    agent: DecisionAgentRequest,
    *,
    failure_reason: str,
    fallback_to_algorithm: bool,
    llm_latency_ms: int | None,
) -> tuple[DecisionResultItem, int | None, bool]:
    """把 LLM 失败、解析失败或超时转换为 fallback/none 响应。

    当实验允许 fallback 且能找到算法推荐出口时，返回 FALLBACK；否则返回 NONE，
    让 Unity 侧按自己的运行逻辑继续处理该人物。
    """

    fallback_exit = _select_fallback_exit(agent)
    if fallback_to_algorithm and fallback_exit is not None:
        logger.info(
            "人物决策使用 fallback 结果: agent_id=%s exit_id=%s reason=%s",
            agent.agent_id,
            fallback_exit.exit_id,
            failure_reason,
        )
        return (
            DecisionResultItem(
                agent_id=agent.agent_id,
                chosen_exit_id=fallback_exit.exit_id,
                decision_source=DecisionSource.FALLBACK,
                reasoning=None,
                confidence=_normalize_confidence(fallback_exit.algorithm_score),
            ),
            llm_latency_ms,
            False,
        )

    logger.info(
        "人物决策返回空结果，等待 Unity 侧后续处理: agent_id=%s reason=%s",
        agent.agent_id,
        failure_reason,
    )
    return (
        DecisionResultItem(
            agent_id=agent.agent_id,
            chosen_exit_id=None,
            decision_source=DecisionSource.NONE,
            reasoning=None,
            confidence=None,
        ),
        llm_latency_ms,
        False,
    )


def _select_fallback_exit(agent: DecisionAgentRequest) -> AvailableExitOption | None:
    """选择 fallback 出口，优先使用算法推荐，否则取 algorithm_score 最高的候选出口。"""

    if agent.algorithm_recommendation is not None:
        for option in agent.available_exits:
            if option.exit_id == agent.algorithm_recommendation:
                return option

    if not agent.available_exits:
        return None

    return max(agent.available_exits, key=lambda option: (option.algorithm_score, option.exit_id))


def _exit_exists(options: list[AvailableExitOption], exit_id: str | None) -> bool:
    if exit_id is None:
        return False
    return any(option.exit_id == exit_id for option in options)


def _was_algorithm_overridden(agent: DecisionAgentRequest, chosen_exit_id: str | None) -> bool:
    if agent.algorithm_recommendation is None or chosen_exit_id is None:
        return False
    return agent.algorithm_recommendation != chosen_exit_id


def _normalize_confidence(value: float | None) -> float | None:
    numeric_value = decimal_to_float(value)
    if numeric_value is None:
        return None
    if numeric_value < 0.0:
        return 0.0
    if numeric_value > 1.0:
        return 1.0
    return round(numeric_value, 4)


def _resolve_batch_status(*, total_count: int, resolved_count: int) -> DecisionBatchStatus:
    """根据已解析人物数把批次归类为成功、部分成功或失败。"""

    if resolved_count <= 0:
        return DecisionBatchStatus.FAILED
    if resolved_count >= total_count:
        return DecisionBatchStatus.SUCCEEDED
    return DecisionBatchStatus.PARTIAL


def _resolve_decision_batch_max_concurrency(agent_count: int) -> int:
    """解析本批次可用并发数，永远不超过配置值和实际人物数量。"""

    settings = get_settings()
    configured_value = max(int(getattr(settings, "decision_batch_max_concurrency", 1)), 1)
    return max(1, min(configured_value, max(agent_count, 1)))


def _calculate_remaining_batch_timeout_seconds(*, started_at: float, hard_timeout_ms: int) -> float | None:
    """计算批次硬超时剩余秒数；返回 None 表示不启用批次级硬超时。"""

    if hard_timeout_ms <= 0:
        return None

    elapsed_seconds = perf_counter() - started_at
    return max((hard_timeout_ms / 1000) - elapsed_seconds, 0.0)


def _is_batch_hard_timeout_reached(started_at: float, hard_timeout_ms: int) -> bool:
    """判断当前批次是否已经达到硬超时阈值。"""

    if hard_timeout_ms <= 0:
        return False
    elapsed_ms = int((perf_counter() - started_at) * 1000)
    return elapsed_ms >= hard_timeout_ms


def _acquire_decision_batch_lock(*, experiment_pk: int, batch_id: str) -> tuple[bool, str | None]:
    """获取同一实验同一 batch_id 的 Redis 锁。

    返回值第一项表示锁机制是否启用；第二项是本请求持有的 token。
    启用但 token 为 None 表示锁被其他请求持有，调用方应返回处理中冲突。
    """

    settings = get_settings()
    ttl_seconds = max(int(settings.decision_batch_lock_ttl_seconds), 0)
    if not settings.redis_enabled or ttl_seconds <= 0:
        return False, None

    lock_token = uuid4().hex
    acquired = get_redis_client().set_if_absent(
        "decisions",
        "batch-lock",
        "exp",
        str(experiment_pk),
        f"batch:{batch_id}",
        value=lock_token,
        ttl_seconds=ttl_seconds,
    )
    if acquired:
        logger.info(
            "决策批次 Redis 批次锁获取成功: experiment_pk=%s batch_id=%s ttl_seconds=%s",
            experiment_pk,
            batch_id,
            ttl_seconds,
        )
        return True, lock_token

    logger.warning(
        "决策批次 Redis 批次锁获取失败: experiment_pk=%s batch_id=%s",
        experiment_pk,
        batch_id,
    )
    return True, None


def _release_decision_batch_lock(
    *,
    experiment_pk: int,
    batch_id: str,
    lock_enabled: bool,
    lock_token: str | None,
) -> None:
    """仅当 token 匹配时释放批次锁，防止误删其他请求续持或重新获得的锁。"""

    if not lock_enabled or not lock_token:
        return

    released = get_redis_client().delete_if_value(
        "decisions",
        "batch-lock",
        "exp",
        str(experiment_pk),
        f"batch:{batch_id}",
        expected_value=lock_token,
    )
    if released:
        logger.info("决策批次 Redis 批次锁已释放: experiment_pk=%s batch_id=%s", experiment_pk, batch_id)


def _acquire_experiment_decision_lock(*, experiment_pk: int) -> tuple[bool, str | None]:
    """获取实验级 Redis 锁，限制同一实验内的决策批次串行执行。"""

    settings = get_settings()
    ttl_seconds = max(int(settings.decision_experiment_lock_ttl_seconds), 0)
    if not settings.redis_enabled or ttl_seconds <= 0:
        return False, None

    lock_token = uuid4().hex
    acquired = get_redis_client().set_if_absent(
        "decisions",
        "experiment-lock",
        "exp",
        str(experiment_pk),
        value=lock_token,
        ttl_seconds=ttl_seconds,
    )
    if acquired:
        logger.info(
            "决策批次 Redis 实验级锁获取成功: experiment_pk=%s ttl_seconds=%s",
            experiment_pk,
            ttl_seconds,
        )
        return True, lock_token

    logger.warning("决策批次 Redis 实验级锁获取失败: experiment_pk=%s", experiment_pk)
    return True, None


def _release_experiment_decision_lock(
    *,
    experiment_pk: int,
    lock_enabled: bool,
    lock_token: str | None,
) -> None:
    """释放实验级 Redis 锁，同样使用 token 校验避免误删。"""

    if not lock_enabled or not lock_token:
        return

    released = get_redis_client().delete_if_value(
        "decisions",
        "experiment-lock",
        "exp",
        str(experiment_pk),
        expected_value=lock_token,
    )
    if released:
        logger.info("决策批次 Redis 实验级锁已释放: experiment_pk=%s", experiment_pk)
