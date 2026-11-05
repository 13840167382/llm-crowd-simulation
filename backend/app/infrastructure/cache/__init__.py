"""缓存基础设施。"""

from backend.app.infrastructure.cache.redis_client import RedisClient, close_redis_client, get_redis_client

__all__ = ["RedisClient", "close_redis_client", "get_redis_client"]
