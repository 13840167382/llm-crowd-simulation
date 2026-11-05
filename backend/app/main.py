"""FastAPI 应用工厂。

这里集中配置应用生命周期、统一异常处理、请求上下文日志、健康检查和 API 路由挂载。
命令行启动 `python -m backend` 时最终会创建这个 app。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from starlette import status

from backend import __version__
from backend.app.api import error_codes
from backend.app.api.errors import register_exception_handlers
from backend.app.api.response import error_response, success_response
from backend.app.api.router import api_router
from backend.app.core.config import get_settings
from backend.app.core.context import reset_request_context, set_request_context
from backend.app.infrastructure.cache import close_redis_client, get_redis_client
from backend.app.infrastructure.observability.logger import configure_logging, get_logger
from backend.app.infrastructure.db.session import check_database_health

logger = get_logger(__name__)
ANALYTICS_DASHBOARD_PATH = Path(__file__).resolve().parent / "static" / "analytics_dashboard.html"
ANALYTICS_DASHBOARD_STYLE_PATH = Path(__file__).resolve().parent / "static" / "analytics_dashboard.css"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """应用生命周期钩子。

    启动时根据环境变量初始化日志；关闭时释放 Redis 连接。
    数据库连接池由 SQLAlchemy engine 管理，按需创建，不在这里强制连接。
    """

    settings = get_settings()
    configure_logging(
        settings.log_level,
        log_to_file=settings.log_to_file,
        log_dir=settings.log_dir,
        log_filename=settings.log_filename,
        log_max_bytes=settings.log_max_bytes,
        log_backup_count=settings.log_backup_count,
    )
    logger.info(
        "应用启动完成: environment=%s host=%s port=%s reload=%s log_to_file=%s log_dir=%s log_filename=%s",
        settings.environment,
        settings.host,
        settings.port,
        settings.reload,
        settings.log_to_file,
        settings.log_dir,
        settings.log_filename,
    )
    try:
        yield
    finally:
        logger.info("应用关闭，开始释放资源")
        close_redis_client()


def create_app() -> FastAPI:
    """创建 FastAPI 应用实例，并挂载所有路由和中间件。"""

    settings = get_settings()
    app = FastAPI(
        title=settings.project_name,
        version=__version__,
        docs_url=f"{settings.api_v1_prefix}/docs",
        redoc_url=f"{settings.api_v1_prefix}/redoc",
        openapi_url=f"{settings.api_v1_prefix}/openapi.json",
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    @app.middleware("http")
    async def request_context_middleware(request: Request, call_next):
        """为每个 HTTP 请求注入 request_id/experiment_id，并写入结构化访问日志。"""

        request_id = request.headers.get("X-Request-Id") or str(uuid4())
        experiment_id = request.headers.get("X-Experiment-Id")
        request.state.request_id = request_id
        request.state.experiment_id = experiment_id

        # contextvars 会被 logger filter 读取，使业务日志自动带上 request_id/experiment_id。
        tokens = set_request_context(request_id=request_id, experiment_id=experiment_id)
        started_at = perf_counter()
        logger.info(
            "收到请求: method=%s path=%s client=%s",
            request.method,
            request.url.path,
            request.client.host if request.client else "-",
        )
        try:
            response = await call_next(request)
            logger.info(
                "请求完成: method=%s path=%s status=%s duration_ms=%s",
                request.method,
                request.url.path,
                response.status_code,
                int((perf_counter() - started_at) * 1000),
            )
        finally:
            reset_request_context(tokens)

        # 把最终 request_id 回写给客户端，便于前后端一起按同一个 ID 查日志。
        response.headers["X-Request-Id"] = request_id
        if experiment_id:
            response.headers["X-Experiment-Id"] = experiment_id
        return response

    @app.get("/", tags=["系统"], summary="服务概览")
    async def service_overview():
        """根路径只返回服务元信息，不承载业务能力。"""

        return success_response(
            data={
                "service": settings.project_name,
                "version": __version__,
                "environment": settings.environment,
                "api_prefix": settings.api_v1_prefix,
                "docs_url": f"{settings.api_v1_prefix}/docs",
            }
        )

    @app.get("/healthz", tags=["系统"], summary="健康检查")
    async def health_check():
        """进程级轻量健康检查，不访问外部依赖。"""

        return success_response(
            data={
                "status": "ok",
                "environment": settings.environment,
            }
        )

    @app.get("/healthz/db", tags=["系统"], summary="数据库健康检查")
    async def database_health_check():
        """数据库健康检查，包含 TimescaleDB 扩展状态。"""

        health = check_database_health()
        logger.debug("数据库健康检查结果: %s", health)
        if health["status"] == "ok":
            return success_response(data=health)

        return error_response(
            code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
            message="存储服务不可用",
            data=health,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/healthz/redis", tags=["系统"], summary="Redis 健康检查")
    async def redis_health_check():
        """Redis 健康检查；未启用 Redis 时视为可用的 disabled 状态。"""

        health = get_redis_client().healthcheck()
        logger.debug("Redis 健康检查结果: %s", health)
        if health["status"] in {"ok", "disabled"}:
            return success_response(data=health)

        return error_response(
            code=error_codes.STORAGE_SERVICE_UNAVAILABLE,
            message="存储服务不可用",
            data=health,
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    @app.get("/ui/analytics", include_in_schema=False)
    async def analytics_dashboard():
        return FileResponse(ANALYTICS_DASHBOARD_PATH, media_type="text/html; charset=utf-8")

    @app.get("/ui/analytics.css", include_in_schema=False)
    async def analytics_dashboard_styles():
        return FileResponse(ANALYTICS_DASHBOARD_STYLE_PATH, media_type="text/css; charset=utf-8")

    app.include_router(api_router, prefix=settings.api_v1_prefix)
    return app
