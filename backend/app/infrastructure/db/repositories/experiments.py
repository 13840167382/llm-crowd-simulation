"""实验仓储实现。"""

from __future__ import annotations

from sqlalchemy import Select, func, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import Experiment, ExperimentAgent, ExperimentAllocation, ExperimentZone


class ExperimentRepository:
    """实验表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, experiment: Experiment) -> Experiment:
        """新增实验记录。"""

        self.session.add(experiment)
        return experiment

    def get_by_experiment_id(self, experiment_id: str) -> Experiment | None:
        """按对外实验编号查询。"""

        stmt = select(Experiment).where(Experiment.experiment_id == experiment_id)
        return self.session.scalar(stmt)

    def has_topology(self, experiment_pk: int) -> bool:
        """判断实验是否已经配置拓扑。"""

        # 这里只关心“是否存在”，因此 count 查询已经足够。
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(ExperimentZone)
            .where(ExperimentZone.experiment_id == experiment_pk)
        )
        return bool(self.session.scalar(stmt))

    def has_allocations(self, experiment_pk: int) -> bool:
        """判断实验是否已经配置人群分配。"""

        # 启动前状态刷新只需要分配是否存在，不需要把完整分配列表查出来。
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(ExperimentAllocation)
            .where(ExperimentAllocation.experiment_id == experiment_pk)
        )
        return bool(self.session.scalar(stmt))

    def has_agents(self, experiment_pk: int) -> bool:
        """判断实验是否已经生成出生点。"""

        # 出生点是否已生成直接影响 start 前校验与 spawn 幂等行为。
        stmt: Select[tuple[int]] = (
            select(func.count())
            .select_from(ExperimentAgent)
            .where(ExperimentAgent.experiment_id == experiment_pk)
        )
        return bool(self.session.scalar(stmt))
