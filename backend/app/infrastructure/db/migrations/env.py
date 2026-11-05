"""Alembic 环境配置。"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.app.core.config import get_settings
from backend.app.infrastructure.db.models import Base
from backend.app.infrastructure.db.models import Experiment  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def normalize_database_url(url: str) -> str:
    """将未显式指定驱动的 PostgreSQL URL 归一化为 psycopg 驱动。"""

    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url


def get_database_url() -> str:
    """获取 Alembic 使用的数据库 URL。"""

    configured_url = config.get_main_option("sqlalchemy.url")
    if configured_url:
        return normalize_database_url(configured_url)

    settings = get_settings()
    return normalize_database_url(settings.alembic_database_url or settings.database_url)


def run_migrations_offline() -> None:
    """离线模式下执行迁移。"""

    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式下执行迁移。"""

    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
