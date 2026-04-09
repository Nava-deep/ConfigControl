from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

import redis

from app.core.metrics import CACHE_EVENTS, CACHE_HITS_TOTAL, CACHE_MISSES_TOTAL, REDIS_AVAILABLE, REDIS_FALLBACK_TOTAL
from app.core.settings import Settings

logger = logging.getLogger(__name__)


class CacheService:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client: redis.Redis | None = None
        self._memory_store: dict[str, dict[str, Any]] = {}
        self._available = False
        if settings.use_redis:
            try:
                self.client = redis.from_url(settings.redis_url, decode_responses=True)
                self.client.ping()
                self._set_available(True)
            except redis.RedisError as exc:
                logger.warning("redis unavailable, continuing without cache: %s", exc)
                REDIS_FALLBACK_TOTAL.labels("startup").inc()
                self._set_available(False)
        else:
            self._set_available(False)

    def is_available(self) -> bool:
        return self._available

    def _set_available(self, value: bool) -> None:
        if self._available and not value:
            CACHE_EVENTS.labels("redis", "availability", "fallback").inc()
        elif not self._available and value:
            CACHE_EVENTS.labels("redis", "availability", "restored").inc()
        self._available = value
        REDIS_AVAILABLE.set(1 if value else 0)

    def get_json(self, key: str) -> dict[str, Any] | None:
        if not self.client:
            payload = self._memory_store.get(key)
            CACHE_EVENTS.labels("memory", "get", "hit" if payload else "miss").inc()
            (CACHE_HITS_TOTAL if payload else CACHE_MISSES_TOTAL).labels("memory").inc()
            return payload
        try:
            raw = self.client.get(key)
            self._set_available(True)
            if raw:
                CACHE_EVENTS.labels("redis", "get", "hit").inc()
                CACHE_HITS_TOTAL.labels("redis").inc()
                return json.loads(raw)
            CACHE_EVENTS.labels("redis", "get", "miss").inc()
            CACHE_MISSES_TOTAL.labels("redis").inc()
            return None
        except (redis.RedisError, json.JSONDecodeError) as exc:
            logger.warning("redis get failed for %s: %s", key, exc)
            REDIS_FALLBACK_TOTAL.labels("get").inc()
            self._set_available(False)
            payload = self._memory_store.get(key)
            CACHE_EVENTS.labels("memory", "get", "hit" if payload else "miss").inc()
            (CACHE_HITS_TOTAL if payload else CACHE_MISSES_TOTAL).labels("memory").inc()
            return payload

    def set_json(self, key: str, payload: dict[str, Any], ttl: int | None = None) -> None:
        self._memory_store[key] = payload
        CACHE_EVENTS.labels("memory", "set", "ok").inc()
        if not self.client:
            return
        try:
            self.client.set(key, json.dumps(payload), ex=ttl)
            self._set_available(True)
            CACHE_EVENTS.labels("redis", "set", "ok").inc()
        except redis.RedisError as exc:
            logger.warning("redis set failed for %s: %s", key, exc)
            REDIS_FALLBACK_TOTAL.labels("set").inc()
            self._set_available(False)
            CACHE_EVENTS.labels("redis", "set", "error").inc()

    def publish(self, channel: str, payload: dict[str, Any]) -> None:
        if not self.client:
            CACHE_EVENTS.labels("memory", "publish", "local_only").inc()
            return
        try:
            self.client.publish(channel, json.dumps(payload))
            self._set_available(True)
            CACHE_EVENTS.labels("redis", "publish", "ok").inc()
        except redis.RedisError as exc:
            logger.warning("redis publish failed for %s: %s", channel, exc)
            REDIS_FALLBACK_TOTAL.labels("publish").inc()
            self._set_available(False)
            CACHE_EVENTS.labels("redis", "publish", "error").inc()

    def set_metric(self, target: str, metric: str, value: float) -> dict[str, Any]:
        record = {
            "target": target,
            "metric": metric,
            "value": value,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        self.set_json(f"metric:{target}:{metric}", record)
        return record

    def get_metric(self, target: str, metric: str) -> dict[str, Any] | None:
        return self.get_json(f"metric:{target}:{metric}")
