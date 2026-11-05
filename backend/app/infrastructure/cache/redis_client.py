"""Redis 客户端与健康检查。

项目把 Redis 用在三类场景：热力图/幂等响应缓存、轻量分布式锁、RQ 队列连接。
普通缓存需要 decode_responses=True 方便读写 JSON 字符串；RQ 需要二进制连接，
所以 RedisClient 内部维护了两种底层 client。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

from redis import Redis
from redis.exceptions import RedisError, WatchError
from sqlalchemy.engine import make_url

from backend.app.core.config import get_settings
from backend.app.infrastructure.observability.logger import get_logger

logger = get_logger(__name__)


@dataclass(slots=True)
class RedisClient:
    """Redis 客户端轻量封装。

    所有业务 key 都必须通过 build_key 加统一前缀，避免开发、测试、生产或多个项目共享
    同一 Redis 时互相污染。
    """

    enabled: bool
    redis_url: str
    key_prefix: str
    socket_timeout_seconds: float
    max_connections: int
    rq_max_connections: int
    _client: Redis | None = field(default=None, init=False, repr=False)
    _binary_client: Redis | None = field(default=None, init=False, repr=False)

    def _get_client(self) -> Redis | None:
        """按需创建 Redis 客户端。"""

        if not self.enabled:
            return None

        if self._client is None:
            masked_url = make_url(self.redis_url).render_as_string(hide_password=True)
            max_connections = max(int(self.max_connections), 1)
            logger.info(
                "初始化 Redis 客户端: redis_url=%s key_prefix=%s max_connections=%s",
                masked_url,
                self.key_prefix,
                max_connections,
            )
            self._client = Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=self.socket_timeout_seconds,
                socket_connect_timeout=self.socket_timeout_seconds,
                max_connections=max_connections,
            )
        return self._client

    def get_connection(self) -> Redis | None:
        """返回底层 Redis 连接，供标准任务框架复用。"""

        return self._get_client()

    def get_rq_connection(self) -> Redis | None:
        """返回 RQ 专用 Redis 连接。

        RQ 自己会序列化 job payload，不能使用 decode_responses=True 的字符串连接。
        """

        if not self.enabled:
            return None

        if self._binary_client is None:
            masked_url = make_url(self.redis_url).render_as_string(hide_password=True)
            rq_max_connections = max(int(self.rq_max_connections), 1)
            logger.info(
                "初始化 RQ Redis 连接: redis_url=%s key_prefix=%s max_connections=%s",
                masked_url,
                self.key_prefix,
                rq_max_connections,
            )
            self._binary_client = Redis.from_url(
                self.redis_url,
                decode_responses=False,
                socket_timeout=self.socket_timeout_seconds,
                socket_connect_timeout=self.socket_timeout_seconds,
                max_connections=rq_max_connections,
            )
        return self._binary_client

    def build_key(self, *parts: object) -> str:
        """拼装带统一前缀的缓存键。"""

        suffix = ":".join(str(part) for part in parts if part is not None and str(part) != "")
        return f"{self.key_prefix}:{suffix}" if suffix else self.key_prefix

    def get_json(self, *parts: object) -> dict[str, Any] | list[Any] | None:
        """读取 JSON 缓存。"""

        client = self._get_client()
        if client is None:
            return None

        cache_key = self.build_key(*parts)
        try:
            raw_value = client.get(cache_key)
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("读取 Redis JSON 缓存失败: key=%s error=%s", cache_key, exc)
            return None

        if raw_value is None:
            return None

        try:
            value = json.loads(raw_value)
        except json.JSONDecodeError:
            logger.warning("Redis JSON 缓存解析失败，已忽略该缓存: key=%s", cache_key)
            return None

        logger.info("Redis JSON 缓存命中: key=%s", cache_key)
        return value

    def set_json(self, *parts: object, value: dict[str, Any] | list[Any], ttl_seconds: int | None = None) -> bool:
        """写入 JSON 缓存。"""

        client = self._get_client()
        if client is None:
            return False

        cache_key = self.build_key(*parts)
        try:
            payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
            if ttl_seconds is not None and ttl_seconds > 0:
                client.set(cache_key, payload, ex=int(ttl_seconds))
            else:
                client.set(cache_key, payload)
        except (TypeError, RedisError, OSError, ValueError) as exc:
            logger.warning("写入 Redis JSON 缓存失败: key=%s error=%s", cache_key, exc)
            return False

        logger.info("Redis JSON 缓存写入完成: key=%s ttl_seconds=%s", cache_key, ttl_seconds)
        return True

    def delete(self, *parts: object) -> int:
        """删除单个缓存键。"""

        client = self._get_client()
        if client is None:
            return 0

        cache_key = self.build_key(*parts)
        try:
            deleted = int(client.delete(cache_key))
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("删除 Redis 缓存失败: key=%s error=%s", cache_key, exc)
            return 0

        if deleted > 0:
            logger.info("Redis 缓存删除完成: key=%s deleted=%s", cache_key, deleted)
        return deleted

    def delete_by_pattern(self, *parts: object) -> int:
        """按模式删除一批缓存键。

        调用方需要在 parts 里显式带通配符，例如 ("heatmap", "*")。
        这里用 scan_iter 而不是 keys，避免在大 Redis 实例上阻塞。
        """

        client = self._get_client()
        if client is None:
            return 0

        pattern = self.build_key(*parts)
        deleted_count = 0
        try:
            keys = list(client.scan_iter(match=pattern))
            if keys:
                deleted_count = int(client.delete(*keys))
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("按模式删除 Redis 缓存失败: pattern=%s error=%s", pattern, exc)
            return 0

        if deleted_count > 0:
            logger.info("Redis 缓存批量删除完成: pattern=%s deleted=%s", pattern, deleted_count)
        return deleted_count

    def set_if_absent(self, *parts: object, value: str, ttl_seconds: int | None = None) -> bool:
        """仅当键不存在时写入字符串值，适合做轻量锁或占位标记。

        返回 True 表示本请求拿到锁；False 表示锁已存在或 Redis 不可用。
        """

        client = self._get_client()
        if client is None:
            return False

        cache_key = self.build_key(*parts)
        try:
            created = bool(client.set(cache_key, value, nx=True, ex=int(ttl_seconds)) if ttl_seconds else client.set(cache_key, value, nx=True))
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("写入 Redis 原子占位键失败: key=%s error=%s", cache_key, exc)
            return False

        if created:
            logger.info("Redis 原子占位键写入完成: key=%s ttl_seconds=%s", cache_key, ttl_seconds)
        return created

    def delete_if_value(self, *parts: object, expected_value: str) -> bool:
        """仅当当前值与预期值一致时删除，避免误释放其他请求持有的锁。

        使用 WATCH/MULTI 实现 compare-and-delete，防止锁 TTL 到期后被别人重新获得时，
        旧请求 finally 块误删新锁。
        """

        client = self._get_client()
        if client is None:
            return False

        cache_key = self.build_key(*parts)
        pipeline = client.pipeline()
        try:
            while True:
                try:
                    pipeline.watch(cache_key)
                    current_value = pipeline.get(cache_key)
                    if current_value != expected_value:
                        pipeline.reset()
                        return False
                    pipeline.multi()
                    pipeline.delete(cache_key)
                    pipeline.execute()
                    logger.info("Redis 原子占位键释放完成: key=%s", cache_key)
                    return True
                except WatchError:
                    pipeline.reset()
                    continue
        except (RedisError, OSError, ValueError) as exc:
            logger.warning("释放 Redis 原子占位键失败: key=%s error=%s", cache_key, exc)
            return False
        finally:
            pipeline.reset()

    def healthcheck(self) -> dict[str, Any]:
        """执行 Redis 健康检查。"""

        if not self.enabled:
            logger.info("Redis 健康检查跳过: enabled=false")
            return {
                "backend": "redis",
                "enabled": False,
                "connected": False,
                "status": "disabled",
                "message": "Redis 未启用",
            }

        client = self._get_client()
        assert client is not None

        try:
            ping_result = client.ping()
        except (RedisError, OSError, ValueError) as exc:
            masked_url = make_url(self.redis_url).render_as_string(hide_password=True)
            logger.warning("Redis 健康检查失败: redis_url=%s error=%s", masked_url, exc)
            return {
                "backend": "redis",
                "enabled": True,
                "connected": False,
                "status": "error",
                "message": f"Redis 连接失败: {exc}",
            }

        logger.info("Redis 健康检查通过: key_prefix=%s", self.key_prefix)
        return {
            "backend": "redis",
            "enabled": True,
            "connected": bool(ping_result),
            "status": "ok",
            "message": "Redis 连接正常",
            "key_prefix": self.key_prefix,
            "max_connections": max(int(self.max_connections), 1),
            "rq_max_connections": max(int(self.rq_max_connections), 1),
        }

    def close(self) -> None:
        """关闭 Redis 客户端连接。"""

        if self._client is None and self._binary_client is None:
            return

        logger.info("关闭 Redis 客户端连接")
        if self._client is not None:
            self._client.close()
            self._client = None
        if self._binary_client is not None:
            self._binary_client.close()
            self._binary_client = None


@lru_cache(maxsize=1)
def get_redis_client() -> RedisClient:
    """获取进程级 Redis 客户端实例。"""

    settings = get_settings()
    return RedisClient(
        enabled=settings.redis_enabled,
        redis_url=settings.redis_url,
        key_prefix=settings.redis_key_prefix,
        socket_timeout_seconds=settings.redis_socket_timeout_seconds,
        max_connections=settings.redis_max_connections,
        rq_max_connections=settings.redis_rq_max_connections,
    )


def close_redis_client() -> None:
    """关闭并重置 Redis 客户端缓存。"""

    if get_redis_client.cache_info().currsize == 0:
        return

    client = get_redis_client()
    client.close()
    get_redis_client.cache_clear()
