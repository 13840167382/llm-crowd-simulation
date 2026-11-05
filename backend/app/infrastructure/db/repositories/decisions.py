"""实验决策仓储实现。"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.orm import Session

from backend.app.domain.enums import DecisionSource
from backend.app.infrastructure.db.models import AgentDecision, DecisionBatch


class DecisionBatchRepository:
    """决策批次表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, batch: DecisionBatch) -> DecisionBatch:
        """新增决策批次记录。"""

        self.session.add(batch)
        return batch

    def add_if_absent(self, batch: DecisionBatch) -> DecisionBatch | None:
        """插入决策批次；同实验同 batch_id 已存在时不抛唯一约束错误。

        这里使用 PostgreSQL ON CONFLICT DO NOTHING，是数据库层幂等的最后防线。
        返回 None 表示其他请求已经先写入了同一个批次。
        """

        stmt = (
            postgresql_insert(DecisionBatch)
            .values(
                experiment_id=batch.experiment_id,
                batch_id=batch.batch_id,
                simulation_time_ms=batch.simulation_time_ms,
                requested_count=batch.requested_count,
                success_count=batch.success_count,
                fallback_count=batch.fallback_count,
                processing_duration_ms=batch.processing_duration_ms,
                status=batch.status,
                response_payload=batch.response_payload,
                processed_at=batch.processed_at,
            )
            .on_conflict_do_nothing(
                constraint="uk_decision_batches_experiment_id_batch_id",
            )
            .returning(DecisionBatch.id)
        )
        inserted_batch_id = self.session.execute(stmt).scalar_one_or_none()
        if inserted_batch_id is None:
            return None
        return self.session.get(DecisionBatch, inserted_batch_id)

    def get_by_experiment_and_batch_id(self, experiment_pk: int, batch_id: str) -> DecisionBatch | None:
        """按实验和批次标识查询决策批次。"""

        # experiment_id + batch_id 是决策幂等的唯一入口。
        stmt = select(DecisionBatch).where(
            DecisionBatch.experiment_id == experiment_pk,
            DecisionBatch.batch_id == batch_id,
        )
        return self.session.scalar(stmt)

    def count_by_experiment_id(self, experiment_pk: int) -> int:
        """统计实验累计决策批次数。"""

        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(DecisionBatch)
            .where(DecisionBatch.experiment_id == experiment_pk)
        )
        return int(self.session.scalar(stmt) or 0)


class AgentDecisionRepository:
    """单个 agent 决策结果仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, decisions: list[AgentDecision]) -> list[AgentDecision]:
        """批量新增 agent 决策结果。"""

        self.session.add_all(decisions)
        return decisions

    def count_by_experiment_and_source(self, experiment_pk: int, source: DecisionSource) -> int:
        """按实验和决策来源统计条数。"""

        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(AgentDecision)
            .where(
                AgentDecision.experiment_id == experiment_pk,
                AgentDecision.decision_source == source,
            )
        )
        return int(self.session.scalar(stmt) or 0)
