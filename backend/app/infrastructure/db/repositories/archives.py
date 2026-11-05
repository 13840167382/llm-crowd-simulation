"""实验归档仓储实现。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.infrastructure.db.models import ExperimentArchive


class ExperimentArchiveRepository:
    """实验归档包基础仓储。"""

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, archive: ExperimentArchive) -> ExperimentArchive:
        """新增实验归档包。"""

        self.session.add(archive)
        self.session.flush()
        return archive

    def get_by_experiment_id(self, experiment_id: str) -> ExperimentArchive | None:
        """按原实验对外编号查询归档包。"""

        stmt = select(ExperimentArchive).where(ExperimentArchive.experiment_id == experiment_id)
        return self.session.scalar(stmt)
