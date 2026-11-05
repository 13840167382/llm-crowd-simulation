"""实验拓扑仓储实现。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import ExperimentExit, ExperimentZone


class ExperimentZoneRepository:
    """实验区域表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, zones: list[ExperimentZone]) -> list[ExperimentZone]:
        """批量新增区域记录。"""

        self.session.add_all(zones)
        return zones

    def list_by_experiment_id(self, experiment_pk: int) -> list[ExperimentZone]:
        """查询实验区域列表。"""

        # 拓扑回放与校验都依赖稳定顺序，因此按主键返回。
        stmt = select(ExperimentZone).where(ExperimentZone.experiment_id == experiment_pk).order_by(ExperimentZone.id)
        return list(self.session.scalars(stmt))

    def delete_by_experiment_id(self, experiment_pk: int) -> None:
        """删除实验下的全部区域。"""

        self.session.execute(delete(ExperimentZone).where(ExperimentZone.experiment_id == experiment_pk))


class ExperimentExitRepository:
    """实验出口表基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add_all(self, exits: list[ExperimentExit]) -> list[ExperimentExit]:
        """批量新增出口记录。"""

        self.session.add_all(exits)
        return exits

    def list_by_experiment_id(self, experiment_pk: int) -> list[ExperimentExit]:
        """查询实验出口列表。"""

        # 出口同样按写入顺序返回，便于联调时和原始请求体逐项对照。
        stmt = select(ExperimentExit).where(ExperimentExit.experiment_id == experiment_pk).order_by(ExperimentExit.id)
        return list(self.session.scalars(stmt))

    def delete_by_experiment_id(self, experiment_pk: int) -> None:
        """删除实验下的全部出口。"""

        self.session.execute(delete(ExperimentExit).where(ExperimentExit.experiment_id == experiment_pk))
