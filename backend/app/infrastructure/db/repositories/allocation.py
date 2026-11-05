"""实验分配与出生点仓储实现。"""

from __future__ import annotations

from sqlalchemy import Select, delete, func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import Experiment, ExperimentAgent, ExperimentAllocation, ExperimentArchive


class ExperimentAllocationRepository:
    """实验分配表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, allocations: list[ExperimentAllocation]) -> list[ExperimentAllocation]:
        """批量新增分配记录。"""

        self.session.add_all(allocations)
        return allocations

    def list_by_experiment_id(self, experiment_pk: int) -> list[ExperimentAllocation]:
        """查询实验分配列表。"""

        # 按主键顺序返回，保证接口展示和快照测试结果稳定。
        stmt = (
            select(ExperimentAllocation)
            .where(ExperimentAllocation.experiment_id == experiment_pk)
            .order_by(ExperimentAllocation.id)
        )
        return list(self.session.scalars(stmt))

    def count_by_type_id(self, type_id: str) -> int:
        """统计某个人群模板被实验分配引用的次数。"""

        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(ExperimentAllocation)
            .where(ExperimentAllocation.type_id == type_id)
        )
        return int(self.session.scalar(stmt) or 0)

    def count_unarchived_experiment_references_by_type_id(self, type_id: str) -> int:
        """统计某个人群模板被未归档实验分配引用的次数。"""

        archive_key_exists = Experiment.summary_json.op("?")("archive")
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(ExperimentAllocation)
            .join(Experiment, Experiment.id == ExperimentAllocation.experiment_id)
            .outerjoin(ExperimentArchive, ExperimentArchive.experiment_id == Experiment.experiment_id)
            .where(ExperimentAllocation.type_id == type_id)
            .where(ExperimentArchive.id.is_(None))
            .where(archive_key_exists.is_(False))
        )
        return int(self.session.scalar(stmt) or 0)

    def delete_by_experiment_id(self, experiment_pk: int) -> None:
        """删除实验下的全部分配。"""

        self.session.execute(delete(ExperimentAllocation).where(ExperimentAllocation.experiment_id == experiment_pk))


class ExperimentAgentRepository:
    """实验出生点表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, agents: list[ExperimentAgent]) -> list[ExperimentAgent]:
        """批量新增出生点记录。"""

        self.session.add_all(agents)
        return agents

    def list_by_experiment_id(self, experiment_pk: int) -> list[ExperimentAgent]:
        """按生成顺序查询实验出生点。"""

        # 优先按 spawn_order 排序，保持 agent_id 与生成顺序一一对应。
        stmt = (
            select(ExperimentAgent)
            .where(ExperimentAgent.experiment_id == experiment_pk)
            .order_by(ExperimentAgent.spawn_order, ExperimentAgent.id)
        )
        return list(self.session.scalars(stmt))

    def delete_by_experiment_id(self, experiment_pk: int) -> None:
        """删除实验下的全部出生点。"""

        self.session.execute(delete(ExperimentAgent).where(ExperimentAgent.experiment_id == experiment_pk))
