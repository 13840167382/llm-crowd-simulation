"""运行时配置。

配置来源是默认常量 + 环境变量 + .env 文件。模块导入时会先加载环境文件，
get_settings() 再把字符串环境变量转换成应用内部使用的类型，并用 lru_cache 缓存。
测试如果需要切换环境变量，需要先 clear get_settings 的缓存。
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from functools import lru_cache

from backend.app.core.constants import (
    DEFAULT_API_V1_PREFIX,
    DEFAULT_ALEMBIC_DATABASE_URL,
    DEFAULT_APP_NAME,
    DEFAULT_DECISION_BATCH_HARD_TIMEOUT_SECONDS,
    DEFAULT_DATABASE_URL,
    DEFAULT_DB_MAX_OVERFLOW,
    DEFAULT_DB_POOL_RECYCLE_SECONDS,
    DEFAULT_DB_POOL_SIZE,
    DEFAULT_DB_POOL_TIMEOUT_SECONDS,
    DEFAULT_DECISION_ENGINE_MODE,
    DEFAULT_DECISION_PROMPT_VERSION,
    DEFAULT_DECISION_BATCH_MAX_CONCURRENCY,
    DEFAULT_DECISION_BATCH_LOCK_TTL_SECONDS,
    DEFAULT_DECISION_EXPERIMENT_LOCK_TTL_SECONDS,
    DEFAULT_DECISION_PROVIDER_API_KEY,
    DEFAULT_DECISION_PROVIDER_BASE_URL,
    DEFAULT_DECISION_PROVIDER_MODEL,
    DEFAULT_DECISION_PROVIDER_TEMPERATURE,
    DEFAULT_ENVIRONMENT,
    DEFAULT_EXPERIMENT_RUNTIME_CLEANUP_AUTO_ENQUEUE,
    DEFAULT_HEATMAP_PREWARM_CELL_SIZE,
    DEFAULT_HEATMAP_PREWARM_ENABLED,
    DEFAULT_HEATMAP_PREWARM_MAX_CROWD_TYPES,
    DEFAULT_TRAJECTORY_QUERY_MAX_RAW_PAGE_SIZE,
    DEFAULT_TRAJECTORY_QUERY_MAX_RAW_WINDOW_MS,
    DEFAULT_TRAJECTORY_QUERY_SAMPLE_INTERVAL_MS,
    DEFAULT_LOG_BACKUP_COUNT,
    DEFAULT_LOG_DIR,
    DEFAULT_LOG_FILENAME,
    DEFAULT_LOG_MAX_BYTES,
    DEFAULT_LOG_TO_FILE,
    DEFAULT_MAINTENANCE_JOB_MAX_RETRY_ATTEMPTS,
    DEFAULT_MAINTENANCE_QUEUE_NAME,
    DEFAULT_MAINTENANCE_RQ_JOB_TIMEOUT_SECONDS,
    DEFAULT_MAINTENANCE_RQ_TTL_SECONDS,
    DEFAULT_MAINTENANCE_RQ_RESULT_TTL_SECONDS,
    DEFAULT_MAINTENANCE_RQ_FAILURE_TTL_SECONDS,
    DEFAULT_MAINTENANCE_JOB_RETRY_BACKOFF_SECONDS,
    DEFAULT_MAINTENANCE_WORKER_IDLE_LOG_EVERY_POLLS,
    DEFAULT_MAINTENANCE_WORKER_POLL_INTERVAL_SECONDS,
    DEFAULT_REQUEST_IDEMPOTENCY_LOCK_TTL_SECONDS,
    DEFAULT_REQUEST_IDEMPOTENCY_TTL_SECONDS,
    DEFAULT_SPAWN_POSITIONS_CACHE_TTL_SECONDS,
    DEFAULT_HOST,
    DEFAULT_LOG_LEVEL,
    DEFAULT_PORT,
    DEFAULT_HEATMAP_CACHE_TTL_SECONDS,
    DEFAULT_REDIS_ENABLED,
    DEFAULT_REDIS_KEY_PREFIX,
    DEFAULT_REDIS_MAX_CONNECTIONS,
    DEFAULT_REDIS_RQ_MAX_CONNECTIONS,
    DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
    DEFAULT_REDIS_URL,
)
from backend.app.core.env_loader import load_environment_files

load_environment_files()

_DECISION_PROVIDER_ENV_PATTERN = re.compile(
    r"^APP_DECISION_PROVIDER_(?P<index>\d+)_(?P<field>BASE_URL|API_KEY|MODEL)$"
)


def _get_bool(name: str, default: bool) -> bool:
    """读取布尔环境变量，支持 1/true/yes/on。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return raw_value.strip().lower() in {"1", "true", "yes", "on"}


def _get_float(name: str, default: float) -> float:
    """读取浮点型环境变量，未设置时返回默认值。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    return float(raw_value)


@dataclass(frozen=True, slots=True)
class DecisionProviderConfig:
    """单个 OpenAI-compatible 模型服务配置。"""

    name: str
    base_url: str
    api_key: str
    model: str = ""


@dataclass(frozen=True, slots=True)
class Settings:
    """应用运行时配置快照。

    dataclass 是 frozen 的，避免运行过程中某个模块意外改配置；需要变更配置时通过环境变量
    和重新创建进程完成。
    """

    # 基础服务与日志配置。
    project_name: str = DEFAULT_APP_NAME
    api_v1_prefix: str = DEFAULT_API_V1_PREFIX
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    reload: bool = False
    log_level: str = DEFAULT_LOG_LEVEL
    log_to_file: bool = DEFAULT_LOG_TO_FILE
    log_dir: str = DEFAULT_LOG_DIR
    log_filename: str = DEFAULT_LOG_FILENAME
    log_max_bytes: int = DEFAULT_LOG_MAX_BYTES
    log_backup_count: int = DEFAULT_LOG_BACKUP_COUNT
    environment: str = DEFAULT_ENVIRONMENT
    env_file: str = ".env.local"
    database_url: str = DEFAULT_DATABASE_URL
    alembic_database_url: str = DEFAULT_ALEMBIC_DATABASE_URL
    db_echo: bool = False
    db_pool_size: int = DEFAULT_DB_POOL_SIZE
    db_max_overflow: int = DEFAULT_DB_MAX_OVERFLOW
    db_pool_timeout_seconds: float = DEFAULT_DB_POOL_TIMEOUT_SECONDS
    db_pool_recycle_seconds: int = DEFAULT_DB_POOL_RECYCLE_SECONDS

    # Redis 既服务缓存/锁，也服务 RQ；redis_enabled=false 时相关能力会自动降级或跳过。
    redis_enabled: bool = DEFAULT_REDIS_ENABLED
    redis_url: str = DEFAULT_REDIS_URL
    redis_key_prefix: str = DEFAULT_REDIS_KEY_PREFIX
    redis_socket_timeout_seconds: float = DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS
    redis_max_connections: int = DEFAULT_REDIS_MAX_CONNECTIONS
    redis_rq_max_connections: int = DEFAULT_REDIS_RQ_MAX_CONNECTIONS
    heatmap_cache_ttl_seconds: int = DEFAULT_HEATMAP_CACHE_TTL_SECONDS
    heatmap_prewarm_enabled: bool = DEFAULT_HEATMAP_PREWARM_ENABLED
    heatmap_prewarm_cell_size: float = DEFAULT_HEATMAP_PREWARM_CELL_SIZE
    heatmap_prewarm_max_crowd_types: int = DEFAULT_HEATMAP_PREWARM_MAX_CROWD_TYPES

    # 分析查询保护项，限制原始轨迹表的大范围扫描。
    trajectory_query_sample_interval_ms: int = DEFAULT_TRAJECTORY_QUERY_SAMPLE_INTERVAL_MS
    trajectory_query_max_raw_window_ms: int = DEFAULT_TRAJECTORY_QUERY_MAX_RAW_WINDOW_MS
    trajectory_query_max_raw_page_size: int = DEFAULT_TRAJECTORY_QUERY_MAX_RAW_PAGE_SIZE
    spawn_positions_cache_ttl_seconds: int = DEFAULT_SPAWN_POSITIONS_CACHE_TTL_SECONDS

    # 决策模型与批次控制配置。
    decision_engine_mode: str = DEFAULT_DECISION_ENGINE_MODE
    decision_provider_base_url: str = DEFAULT_DECISION_PROVIDER_BASE_URL
    decision_provider_api_key: str = DEFAULT_DECISION_PROVIDER_API_KEY
    decision_provider_model: str = DEFAULT_DECISION_PROVIDER_MODEL
    decision_providers: tuple[DecisionProviderConfig, ...] = ()
    decision_provider_temperature: float = DEFAULT_DECISION_PROVIDER_TEMPERATURE
    decision_prompt_version: str = DEFAULT_DECISION_PROMPT_VERSION
    decision_batch_hard_timeout_seconds: float = DEFAULT_DECISION_BATCH_HARD_TIMEOUT_SECONDS
    decision_batch_max_concurrency: int = DEFAULT_DECISION_BATCH_MAX_CONCURRENCY
    decision_batch_lock_ttl_seconds: int = DEFAULT_DECISION_BATCH_LOCK_TTL_SECONDS
    decision_experiment_lock_ttl_seconds: int = DEFAULT_DECISION_EXPERIMENT_LOCK_TTL_SECONDS
    request_idempotency_ttl_seconds: int = DEFAULT_REQUEST_IDEMPOTENCY_TTL_SECONDS
    request_idempotency_lock_ttl_seconds: int = DEFAULT_REQUEST_IDEMPOTENCY_LOCK_TTL_SECONDS

    # 后台维护任务/RQ worker 配置。
    maintenance_queue_name: str = DEFAULT_MAINTENANCE_QUEUE_NAME
    maintenance_rq_job_timeout_seconds: int = DEFAULT_MAINTENANCE_RQ_JOB_TIMEOUT_SECONDS
    maintenance_rq_ttl_seconds: int = DEFAULT_MAINTENANCE_RQ_TTL_SECONDS
    maintenance_rq_result_ttl_seconds: int = DEFAULT_MAINTENANCE_RQ_RESULT_TTL_SECONDS
    maintenance_rq_failure_ttl_seconds: int = DEFAULT_MAINTENANCE_RQ_FAILURE_TTL_SECONDS
    maintenance_worker_poll_interval_seconds: float = DEFAULT_MAINTENANCE_WORKER_POLL_INTERVAL_SECONDS
    maintenance_worker_idle_log_every_polls: int = DEFAULT_MAINTENANCE_WORKER_IDLE_LOG_EVERY_POLLS
    maintenance_job_max_retry_attempts: int = DEFAULT_MAINTENANCE_JOB_MAX_RETRY_ATTEMPTS
    maintenance_job_retry_backoff_seconds: float = DEFAULT_MAINTENANCE_JOB_RETRY_BACKOFF_SECONDS
    experiment_runtime_cleanup_auto_enqueue: bool = DEFAULT_EXPERIMENT_RUNTIME_CLEANUP_AUTO_ENQUEUE


@lru_cache
def get_settings() -> Settings:
    """读取并缓存运行时配置。"""

    environment = os.getenv("APP_ENV", DEFAULT_ENVIRONMENT).strip().lower() or DEFAULT_ENVIRONMENT
    decision_provider_base_url = os.getenv(
        "APP_DECISION_PROVIDER_BASE_URL",
        DEFAULT_DECISION_PROVIDER_BASE_URL,
    ).strip()
    decision_provider_api_key = os.getenv(
        "APP_DECISION_PROVIDER_API_KEY",
        DEFAULT_DECISION_PROVIDER_API_KEY,
    ).strip()
    decision_provider_model = os.getenv(
        "APP_DECISION_PROVIDER_MODEL",
        DEFAULT_DECISION_PROVIDER_MODEL,
    ).strip()

    return Settings(
        project_name=os.getenv("APP_NAME", DEFAULT_APP_NAME),
        api_v1_prefix=os.getenv("APP_API_V1_PREFIX", DEFAULT_API_V1_PREFIX),
        host=os.getenv("APP_HOST", DEFAULT_HOST),
        port=int(os.getenv("APP_PORT", str(DEFAULT_PORT))),
        reload=_get_bool("APP_RELOAD", False),
        log_level=os.getenv("APP_LOG_LEVEL", DEFAULT_LOG_LEVEL),
        log_to_file=_get_bool("APP_LOG_TO_FILE", DEFAULT_LOG_TO_FILE),
        log_dir=os.getenv("APP_LOG_DIR", DEFAULT_LOG_DIR),
        log_filename=os.getenv("APP_LOG_FILENAME", DEFAULT_LOG_FILENAME),
        log_max_bytes=int(os.getenv("APP_LOG_MAX_BYTES", str(DEFAULT_LOG_MAX_BYTES))),
        log_backup_count=int(os.getenv("APP_LOG_BACKUP_COUNT", str(DEFAULT_LOG_BACKUP_COUNT))),
        environment=environment,
        env_file=f".env.{environment}",
        database_url=os.getenv("APP_DATABASE_URL", DEFAULT_DATABASE_URL),
        alembic_database_url=os.getenv("APP_ALEMBIC_DATABASE_URL", DEFAULT_ALEMBIC_DATABASE_URL),
        db_echo=_get_bool("APP_DB_ECHO", False),
        db_pool_size=int(os.getenv("APP_DB_POOL_SIZE", str(DEFAULT_DB_POOL_SIZE))),
        db_max_overflow=int(os.getenv("APP_DB_MAX_OVERFLOW", str(DEFAULT_DB_MAX_OVERFLOW))),
        db_pool_timeout_seconds=_get_float(
            "APP_DB_POOL_TIMEOUT_SECONDS",
            DEFAULT_DB_POOL_TIMEOUT_SECONDS,
        ),
        db_pool_recycle_seconds=int(
            os.getenv("APP_DB_POOL_RECYCLE_SECONDS", str(DEFAULT_DB_POOL_RECYCLE_SECONDS))
        ),
        redis_enabled=_get_bool("APP_REDIS_ENABLED", DEFAULT_REDIS_ENABLED),
        redis_url=os.getenv("APP_REDIS_URL", DEFAULT_REDIS_URL),
        redis_key_prefix=os.getenv("APP_REDIS_KEY_PREFIX", DEFAULT_REDIS_KEY_PREFIX),
        redis_socket_timeout_seconds=_get_float(
            "APP_REDIS_SOCKET_TIMEOUT_SECONDS",
            DEFAULT_REDIS_SOCKET_TIMEOUT_SECONDS,
        ),
        redis_max_connections=int(
            os.getenv("APP_REDIS_MAX_CONNECTIONS", str(DEFAULT_REDIS_MAX_CONNECTIONS))
        ),
        redis_rq_max_connections=int(
            os.getenv("APP_REDIS_RQ_MAX_CONNECTIONS", str(DEFAULT_REDIS_RQ_MAX_CONNECTIONS))
        ),
        heatmap_cache_ttl_seconds=int(
            os.getenv("APP_HEATMAP_CACHE_TTL_SECONDS", str(DEFAULT_HEATMAP_CACHE_TTL_SECONDS))
        ),
        heatmap_prewarm_enabled=_get_bool(
            "APP_HEATMAP_PREWARM_ENABLED",
            DEFAULT_HEATMAP_PREWARM_ENABLED,
        ),
        heatmap_prewarm_cell_size=_get_float(
            "APP_HEATMAP_PREWARM_CELL_SIZE",
            DEFAULT_HEATMAP_PREWARM_CELL_SIZE,
        ),
        heatmap_prewarm_max_crowd_types=int(
            os.getenv(
                "APP_HEATMAP_PREWARM_MAX_CROWD_TYPES",
                str(DEFAULT_HEATMAP_PREWARM_MAX_CROWD_TYPES),
            )
        ),
        trajectory_query_sample_interval_ms=int(
            os.getenv(
                "APP_TRAJECTORY_QUERY_SAMPLE_INTERVAL_MS",
                str(DEFAULT_TRAJECTORY_QUERY_SAMPLE_INTERVAL_MS),
            )
        ),
        trajectory_query_max_raw_window_ms=int(
            os.getenv(
                "APP_TRAJECTORY_QUERY_MAX_RAW_WINDOW_MS",
                str(DEFAULT_TRAJECTORY_QUERY_MAX_RAW_WINDOW_MS),
            )
        ),
        trajectory_query_max_raw_page_size=int(
            os.getenv(
                "APP_TRAJECTORY_QUERY_MAX_RAW_PAGE_SIZE",
                str(DEFAULT_TRAJECTORY_QUERY_MAX_RAW_PAGE_SIZE),
            )
        ),
        spawn_positions_cache_ttl_seconds=int(
            os.getenv(
                "APP_SPAWN_POSITIONS_CACHE_TTL_SECONDS",
                str(DEFAULT_SPAWN_POSITIONS_CACHE_TTL_SECONDS),
            )
        ),
        decision_engine_mode=os.getenv(
            "APP_DECISION_ENGINE_MODE",
            DEFAULT_DECISION_ENGINE_MODE,
        ).strip().lower(),
        decision_provider_base_url=decision_provider_base_url,
        decision_provider_api_key=decision_provider_api_key,
        decision_provider_model=decision_provider_model,
        decision_providers=_load_decision_provider_configs(
            primary_base_url=decision_provider_base_url,
            primary_api_key=decision_provider_api_key,
            primary_model=decision_provider_model,
        ),
        decision_provider_temperature=_get_float(
            "APP_DECISION_PROVIDER_TEMPERATURE",
            DEFAULT_DECISION_PROVIDER_TEMPERATURE,
        ),
        decision_prompt_version=os.getenv(
            "APP_DECISION_PROMPT_VERSION",
            DEFAULT_DECISION_PROMPT_VERSION,
        ).strip() or DEFAULT_DECISION_PROMPT_VERSION,
        decision_batch_hard_timeout_seconds=_get_float(
            "APP_DECISION_BATCH_HARD_TIMEOUT_SECONDS",
            DEFAULT_DECISION_BATCH_HARD_TIMEOUT_SECONDS,
        ),
        decision_batch_max_concurrency=int(
            os.getenv(
                "APP_DECISION_BATCH_MAX_CONCURRENCY",
                str(DEFAULT_DECISION_BATCH_MAX_CONCURRENCY),
            )
        ),
        decision_batch_lock_ttl_seconds=int(
            os.getenv(
                "APP_DECISION_BATCH_LOCK_TTL_SECONDS",
                str(DEFAULT_DECISION_BATCH_LOCK_TTL_SECONDS),
            )
        ),
        decision_experiment_lock_ttl_seconds=int(
            os.getenv(
                "APP_DECISION_EXPERIMENT_LOCK_TTL_SECONDS",
                str(DEFAULT_DECISION_EXPERIMENT_LOCK_TTL_SECONDS),
            )
        ),
        request_idempotency_ttl_seconds=int(
            os.getenv(
                "APP_REQUEST_IDEMPOTENCY_TTL_SECONDS",
                str(DEFAULT_REQUEST_IDEMPOTENCY_TTL_SECONDS),
            )
        ),
        request_idempotency_lock_ttl_seconds=int(
            os.getenv(
                "APP_REQUEST_IDEMPOTENCY_LOCK_TTL_SECONDS",
                str(DEFAULT_REQUEST_IDEMPOTENCY_LOCK_TTL_SECONDS),
            )
        ),
        maintenance_queue_name=os.getenv(
            "APP_MAINTENANCE_QUEUE_NAME",
            DEFAULT_MAINTENANCE_QUEUE_NAME,
        ),
        maintenance_rq_job_timeout_seconds=int(
            os.getenv(
                "APP_MAINTENANCE_RQ_JOB_TIMEOUT_SECONDS",
                str(DEFAULT_MAINTENANCE_RQ_JOB_TIMEOUT_SECONDS),
            )
        ),
        maintenance_rq_ttl_seconds=int(
            os.getenv(
                "APP_MAINTENANCE_RQ_TTL_SECONDS",
                str(DEFAULT_MAINTENANCE_RQ_TTL_SECONDS),
            )
        ),
        maintenance_rq_result_ttl_seconds=int(
            os.getenv(
                "APP_MAINTENANCE_RQ_RESULT_TTL_SECONDS",
                str(DEFAULT_MAINTENANCE_RQ_RESULT_TTL_SECONDS),
            )
        ),
        maintenance_rq_failure_ttl_seconds=int(
            os.getenv(
                "APP_MAINTENANCE_RQ_FAILURE_TTL_SECONDS",
                str(DEFAULT_MAINTENANCE_RQ_FAILURE_TTL_SECONDS),
            )
        ),
        maintenance_worker_poll_interval_seconds=_get_float(
            "APP_MAINTENANCE_WORKER_POLL_INTERVAL_SECONDS",
            DEFAULT_MAINTENANCE_WORKER_POLL_INTERVAL_SECONDS,
        ),
        maintenance_worker_idle_log_every_polls=int(
            os.getenv(
                "APP_MAINTENANCE_WORKER_IDLE_LOG_EVERY_POLLS",
                str(DEFAULT_MAINTENANCE_WORKER_IDLE_LOG_EVERY_POLLS),
            )
        ),
        maintenance_job_max_retry_attempts=int(
            os.getenv(
                "APP_MAINTENANCE_JOB_MAX_RETRY_ATTEMPTS",
                str(DEFAULT_MAINTENANCE_JOB_MAX_RETRY_ATTEMPTS),
            )
        ),
        maintenance_job_retry_backoff_seconds=_get_float(
            "APP_MAINTENANCE_JOB_RETRY_BACKOFF_SECONDS",
            DEFAULT_MAINTENANCE_JOB_RETRY_BACKOFF_SECONDS,
        ),
        experiment_runtime_cleanup_auto_enqueue=_get_bool(
            "APP_EXPERIMENT_RUNTIME_CLEANUP_AUTO_ENQUEUE",
            DEFAULT_EXPERIMENT_RUNTIME_CLEANUP_AUTO_ENQUEUE,
        ),
    )


def _load_decision_provider_configs(
    *,
    primary_base_url: str,
    primary_api_key: str,
    primary_model: str,
) -> tuple[DecisionProviderConfig, ...]:
    """解析决策模型 provider 池。

    旧的 APP_DECISION_PROVIDER_* 配置继续作为第 1 个 provider；编号从 2 开始的
    APP_DECISION_PROVIDER_2_* / APP_DECISION_PROVIDER_3_* 作为额外 provider。额外 provider
    必须配置 API Key 才会启用，避免 .env 示例里的空占位值参与轮询。
    """

    extra_providers = _load_extra_decision_provider_configs(
        primary_base_url=primary_base_url,
        primary_model=primary_model,
    )
    providers: list[DecisionProviderConfig] = []

    if primary_api_key or not extra_providers:
        providers.append(
            DecisionProviderConfig(
                name="provider_1",
                base_url=primary_base_url,
                api_key=primary_api_key,
                model=primary_model,
            )
        )
    providers.extend(extra_providers)
    return tuple(providers)


def _load_extra_decision_provider_configs(
    *,
    primary_base_url: str,
    primary_model: str,
) -> list[DecisionProviderConfig]:
    """读取编号 provider 配置，编号 2 及以后表示额外模型服务。"""

    raw_by_index: dict[int, dict[str, str]] = {}
    for env_name, env_value in os.environ.items():
        match = _DECISION_PROVIDER_ENV_PATTERN.match(env_name)
        if match is None:
            continue
        index = int(match.group("index"))
        if index < 2:
            continue
        raw_by_index.setdefault(index, {})[match.group("field")] = env_value.strip()

    providers: list[DecisionProviderConfig] = []
    for index in sorted(raw_by_index):
        raw_provider = raw_by_index[index]
        api_key = raw_provider.get("API_KEY", "").strip()
        if not api_key:
            continue
        providers.append(
            DecisionProviderConfig(
                name=f"provider_{index}",
                base_url=raw_provider.get("BASE_URL", "").strip() or primary_base_url,
                api_key=api_key,
                model=raw_provider.get("MODEL", "").strip() or primary_model,
            )
        )
    return providers
