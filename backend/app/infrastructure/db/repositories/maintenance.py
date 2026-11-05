"""实验维护任务仓储实现。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Select, and_, exists, or_, select
from sqlalchemy.orm import Session
from sqlalchemy.orm import aliased

from backend.app.domain.enums import MaintenanceJobStatus, MaintenanceJobType
from backend.app.infrastructure.db.models import ExperimentMaintenanceJob


class ExperimentMaintenanceJobRepository:
    """实验维护任务队列仓储。

    维护任务虽然可以被 RQ 唤醒执行，但调度顺序、状态和重试次数都以数据库记录为准。
    仓储层负责提供“按实验链路顺序执行”和“跳过已被其他 worker 锁住的任务”的查询能力。
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def add(self, job: ExperimentMaintenanceJob) -> ExperimentMaintenanceJob:
        """新增维护任务。"""

        self.session.add(job)
        return job

    def get_latest_by_experiment_and_type(
        self,
        *,
        experiment_pk: int,
        job_type: MaintenanceJobType,
    ) -> ExperimentMaintenanceJob | None:
        """查询实验某类任务的最新一条记录。"""

        stmt = (
            select(ExperimentMaintenanceJob)
            .where(
                ExperimentMaintenanceJob.experiment_id == experiment_pk,
                ExperimentMaintenanceJob.job_type == job_type,
            )
            .order_by(ExperimentMaintenanceJob.created_at.desc(), ExperimentMaintenanceJob.id.desc())
        )
        return self.session.scalar(stmt)

    def get_by_id(self, job_id: int) -> ExperimentMaintenanceJob | None:
        """按主键查询维护任务。"""

        return self.session.get(ExperimentMaintenanceJob, job_id)

    def list_by_experiment(
        self,
        *,
        experiment_pk: int,
        statuses: set[MaintenanceJobStatus] | None = None,
        limit: int | None = None,
        order_desc: bool = False,
    ) -> list[ExperimentMaintenanceJob]:
        """按实验列出维护任务。"""

        stmt = select(ExperimentMaintenanceJob).where(ExperimentMaintenanceJob.experiment_id == experiment_pk)
        if statuses:
            stmt = stmt.where(ExperimentMaintenanceJob.status.in_(list(statuses)))
        if order_desc:
            stmt = stmt.order_by(ExperimentMaintenanceJob.created_at.desc(), ExperimentMaintenanceJob.id.desc())
        else:
            stmt = stmt.order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
        if limit is not None:
            stmt = stmt.limit(limit)
        return list(self.session.scalars(stmt))

    def list_from_job(
        self,
        *,
        experiment_pk: int,
        from_created_at: datetime,
        from_job_id: int,
    ) -> list[ExperimentMaintenanceJob]:
        """列出某个任务之后（含当前任务）的维护任务链路。"""

        stmt = (
            select(ExperimentMaintenanceJob)
            .where(
                ExperimentMaintenanceJob.experiment_id == experiment_pk,
                or_(
                    ExperimentMaintenanceJob.created_at > from_created_at,
                    and_(
                        ExperimentMaintenanceJob.created_at == from_created_at,
                        ExperimentMaintenanceJob.id >= from_job_id,
                    ),
                ),
            )
            .order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
        )
        return list(self.session.scalars(stmt))

    def list_pending_jobs(self, *, limit: int, lock_rows: bool = True) -> list[ExperimentMaintenanceJob]:
        """按创建顺序拉取待执行任务。

        查询会保证同一实验内早创建的 pending/running/failed 任务先处理完，后续任务才会被取出。
        lock_rows=True 时使用 FOR UPDATE SKIP LOCKED，多个 worker 并发时不会拿到同一行。
        """

        now = datetime.now(timezone.utc)
        older_job = aliased(ExperimentMaintenanceJob)
        stmt: Select[tuple[ExperimentMaintenanceJob]] = (
            select(ExperimentMaintenanceJob)
            .where(
                ExperimentMaintenanceJob.status == MaintenanceJobStatus.PENDING,
                (
                    ExperimentMaintenanceJob.scheduled_at.is_(None)
                    | (ExperimentMaintenanceJob.scheduled_at <= now)
                ),
                # 同一实验维护链路要按创建顺序串行推进；前序任务未完成时不取后序任务。
                ~exists(
                    select(1).where(
                        older_job.experiment_id == ExperimentMaintenanceJob.experiment_id,
                        older_job.id != ExperimentMaintenanceJob.id,
                        or_(
                            older_job.created_at < ExperimentMaintenanceJob.created_at,
                            and_(
                                older_job.created_at == ExperimentMaintenanceJob.created_at,
                                older_job.id < ExperimentMaintenanceJob.id,
                            ),
                        ),
                        older_job.status.in_(
                            [
                                MaintenanceJobStatus.PENDING,
                                MaintenanceJobStatus.RUNNING,
                                MaintenanceJobStatus.FAILED,
                            ]
                        ),
                    )
                ),
            )
            .order_by(ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
            .limit(limit)
        )
        if lock_rows:
            stmt = stmt.with_for_update(skip_locked=True)
        return list(self.session.scalars(stmt))

    def get_next_pending_scheduled_at(self) -> datetime | None:
        """返回下一条待执行任务的计划时间，用于延迟重试或补偿调度。"""

        stmt = (
            select(ExperimentMaintenanceJob.scheduled_at)
            .where(
                ExperimentMaintenanceJob.status == MaintenanceJobStatus.PENDING,
                ExperimentMaintenanceJob.scheduled_at.is_not(None),
            )
            .order_by(ExperimentMaintenanceJob.scheduled_at, ExperimentMaintenanceJob.created_at, ExperimentMaintenanceJob.id)
            .limit(1)
        )
        return self.session.scalar(stmt)

    def mark_running(self, job: ExperimentMaintenanceJob, *, started_at: datetime) -> ExperimentMaintenanceJob:
        """标记任务开始执行。"""

        job.status = MaintenanceJobStatus.RUNNING
        job.started_at = started_at
        job.attempt_count = int(job.attempt_count or 0) + 1
        job.error_message = None
        return job

    def mark_succeeded(
        self,
        job: ExperimentMaintenanceJob,
        *,
        result_json: dict[str, object],
        finished_at: datetime,
    ) -> ExperimentMaintenanceJob:
        """标记任务执行成功。"""

        job.status = MaintenanceJobStatus.SUCCEEDED
        job.finished_at = finished_at
        job.result_json = result_json
        job.error_message = None
        return job

    def mark_failed(
        self,
        job: ExperimentMaintenanceJob,
        *,
        error_message: str,
        finished_at: datetime,
    ) -> ExperimentMaintenanceJob:
        """标记任务执行失败。"""

        job.status = MaintenanceJobStatus.FAILED
        job.finished_at = finished_at
        job.error_message = error_message
        return job

    def mark_retry_pending(
        self,
        job: ExperimentMaintenanceJob,
        *,
        error_message: str,
        scheduled_at: datetime,
    ) -> ExperimentMaintenanceJob:
        """把失败任务重新放回待执行状态，并写入下一次调度时间。"""

        job.status = MaintenanceJobStatus.PENDING
        job.started_at = None
        job.finished_at = None
        job.scheduled_at = scheduled_at
        job.error_message = error_message
        return job

    def reset_for_replay(
        self,
        job: ExperimentMaintenanceJob,
        *,
        scheduled_at: datetime,
        reset_attempt_count: bool = True,
    ) -> ExperimentMaintenanceJob:
        """把任务重置为待执行，供人工重放或补跑链路使用。"""

        job.status = MaintenanceJobStatus.PENDING
        job.started_at = None
        job.finished_at = None
        job.scheduled_at = scheduled_at
        job.error_message = None
        job.result_json = {}
        if reset_attempt_count:
            job.attempt_count = 0
        return job
