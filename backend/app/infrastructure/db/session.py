"""数据库引擎、会话与健康检查。

应用只在这里创建 SQLAlchemy Engine 和 Session factory。业务层拿到 Session 后自行控制
flush/commit/rollback；FastAPI 请求结束时只负责关闭连接，避免隐藏提交造成数据不一致。
"""

from __future__ import annotations

from collections.abc import Generator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from backend.app.core.config import get_settings
from backend.app.infrastructure.db.timescale import inspect_timescaledb
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


@lru_cache
def get_engine() -> Engine:
    """创建并缓存数据库引擎。

    pool_pre_ping=True 可以在连接被数据库或网络中断后自动探测并重建，减少长时间运行服务的
    stale connection 问题。
    """

    settings = get_settings()
    masked_url = make_url(settings.database_url).render_as_string(hide_password=True)
    pool_size = max(int(settings.db_pool_size), 1)
    max_overflow = max(int(settings.db_max_overflow), 0)
    pool_timeout = max(float(settings.db_pool_timeout_seconds), 1.0)
    pool_recycle = int(settings.db_pool_recycle_seconds) if int(settings.db_pool_recycle_seconds) > 0 else -1
    logger.info(
        "初始化数据库引擎: database_url=%s pool_size=%s max_overflow=%s pool_timeout_seconds=%s pool_recycle_seconds=%s",
        masked_url,
        pool_size,
        max_overflow,
        pool_timeout,
        pool_recycle,
    )
    return create_engine(
        settings.database_url,
        echo=settings.db_echo,
        future=True,
        pool_pre_ping=True,
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
    )


@lru_cache
def get_session_factory() -> sessionmaker[Session]:
    """创建并缓存会话工厂。

    expire_on_commit=False 让 service 在 commit 后仍能读取刚写入对象的字段，简化响应组装。
    """

    return sessionmaker(
        bind=get_engine(),
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
        class_=Session,
    )


def get_db_session() -> Generator[Session, None, None]:
    """FastAPI 依赖使用的数据库会话。"""

    session = get_session_factory()()
    try:
        yield session
    finally:
        # 每个请求作用域结束后都主动关闭会话，避免连接长时间悬挂。
        session.close()


def check_database_health() -> dict[str, Any]:
    """执行数据库健康检查。

    除了 SELECT 1，还会读取 PostgreSQL 版本和 TimescaleDB 状态，因为轨迹表依赖 hypertable
    能力，单纯数据库可连并不代表分析链路完整可用。
    """

    settings = get_settings()
    database_name = make_url(settings.database_url).database

    try:
        with get_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
            server_version = connection.execute(text("SELECT version()")).scalar()
            timescaledb = inspect_timescaledb(connection)
    except (SQLAlchemyError, OSError, ValueError) as exc:
        logger.warning("数据库健康检查失败: database=%s error=%s", database_name, exc)
        return {
            "backend": "postgresql",
            "connected": False,
            "status": "error",
            "database": database_name,
            "message": f"数据库连接失败: {exc}",
        }

    logger.info(
        "数据库健康检查通过: database=%s timescaledb_ready=%s",
        database_name,
        timescaledb["ready"],
    )
    return {
        "backend": "postgresql",
        "connected": True,
        "status": "ok",
        "database": database_name,
        "server_version": server_version,
        "timescaledb": timescaledb,
        "message": "数据库连接正常",
    }
