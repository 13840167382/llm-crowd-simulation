"""实验归档 ORM 模型。"""

from __future__ import annotations

from typing import Any

from sqlalchemy import BigInteger, Boolean, Index, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from backend.app.infrastructure.db.base import Base, BigIntPrimaryKeyMixin, TimestampMixin


class ExperimentArchive(Base, BigIntPrimaryKeyMixin, TimestampMixin):
    """独立于在线实验关系的实验归档包。"""

    __tablename__ = "experiment_archives"
    __table_args__ = (
        UniqueConstraint("experiment_id", name="uk_experiment_archives_experiment_id"),
        Index("idx_experiment_archives_archived_at_ms", "archived_at_ms"),
        Index("idx_experiment_archives_source_status", "source_status"),
        {"comment": "实验归档包"},
    )

    experiment_id: Mapped[str] = mapped_column(String(64), nullable=False, comment="原实验对外编号")
    experiment_name: Mapped[str] = mapped_column(String(128), nullable=False, comment="原实验名称")
    source_status: Mapped[str] = mapped_column(String(32), nullable=False, comment="归档时实验状态")
    archived_at_ms: Mapped[int] = mapped_column(BigInteger, nullable=False, comment="归档时间，毫秒")
    archive_version: Mapped[str] = mapped_column(String(32), nullable=False, comment="归档结构版本")
    archive_payload_json: Mapped[dict[str, Any]] = mapped_column(
        "archive_payload",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="自包含归档包",
    )
    summary_json: Mapped[dict[str, Any]] = mapped_column(
        "summary",
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="轻量归档摘要",
    )
    raw_runtime_retained: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("false"),
        comment="归档后是否仍保留原始运行明细",
    )
    archive_location: Mapped[str | None] = mapped_column(Text, nullable=True, comment="外部归档位置")
