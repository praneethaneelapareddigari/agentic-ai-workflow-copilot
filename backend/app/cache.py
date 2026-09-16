"""
Cache abstraction used to memoize idempotent tool calls and LLM completions.

Tries Redis first (production topology: shared cache across replicas). If
Redis is unreachable — no server configured, connection refused, timeout —
it transparently degrades to a process-local TTL dict so the app, and its
test suite, never hard-fail just because Redis isn't running.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import time
from typing import Any, Optional

from app.config import get_settings
from app.observability import METRICS, get_logger

logger = get_logger(__name__)
settings = get_settings()


def make_cache_key(*parts: Any) -> str:
    raw = json.dumps(parts, sort_keys=True, default=str)
    digest = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return f"{settings.CACHE_NAMESPACE}:{digest}"


class InMemoryCache:
    """Simple TTL dict cache, safe under asyncio (single event loop)."""

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, str]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[str]:
        async with self._lock:
            item = self._store.get(key)
            if not item:
                return None
            expires_at, value = item
            if expires_at < time.time():
                self._store.pop(key, None)
                return None
            return value

    async def set(self, key: str, value: str, ttl: int) -> None:
        async with self._lock:
            self._store[key] = (time.time() + ttl, value)

    async def delete(self, key: str) -> None:
        async with self._lock:
            self._store.pop(key, None)

    def size(self) -> int:
        return len(self._store)


class CacheManager:
    """Facade that prefers Redis, falls back to in-memory automatically."""

    def __init__(self) -> None:
        self._memory = InMemoryCache()
        self._redis = None
        self._backend_name = "memory"
        self._redis_checked = False

    async def _get_redis(self):
        if self._redis_checked:
            return self._redis
        self._redis_checked = True
        if not settings.REDIS_URL:
            return None
        try:
            import redis.asyncio as aioredis  # imported lazily; optional dep

            client = aioredis.from_url(
                settings.REDIS_URL, socket_connect_timeout=0.5, socket_timeout=0.5
            )
            await client.ping()
            self._redis = client
            self._backend_name = "redis"
            logger.info("Connected to Redis cache backend")
        except Exception as exc:  # noqa: BLE001 - deliberately broad, this is a fallback path
            logger.info(f"Redis unavailable ({exc}); falling back to in-memory cache")
            self._redis = None
        return self._redis

    @property
    def backend_name(self) -> str:
        return self._backend_name

    async def get_json(self, key: str) -> Optional[Any]:
        redis = await self._get_redis()
        raw: Optional[str] = None
        try:
            if redis is not None:
                raw = await redis.get(key)
            else:
                raw = await self._memory.get(key)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"Cache GET failed, treating as miss: {exc}")
            raw = None

        if raw is None:
            METRICS.incr("cache.miss")
            return None
        METRICS.incr("cache.hit")
        return json.loads(raw)

    async def set_json(self, key: str, value: Any, ttl: Optional[int] = None) -> None:
        ttl = ttl or settings.CACHE_TTL_SECONDS
        payload = json.dumps(value, default=str)
        redis = await self._get_redis()
        try:
            if redis is not None:
                await redis.set(key, payload, ex=ttl)
            else:
                await self._memory.set(key, payload, ttl)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"Cache SET failed, ignoring: {exc}")

    async def invalidate(self, key: str) -> None:
        redis = await self._get_redis()
        try:
            if redis is not None:
                await redis.delete(key)
            else:
                await self._memory.delete(key)
        except Exception as exc:  # noqa: BLE001
            logger.info(f"Cache DELETE failed, ignoring: {exc}")


cache_manager = CacheManager()
