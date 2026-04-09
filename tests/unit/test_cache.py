from __future__ import annotations

import redis

from app.core.settings import Settings
from app.services.cache import CacheService


class FailingRedisClient:
    def get(self, key: str):
        raise redis.RedisError(f"boom reading {key}")

    def set(self, key: str, value: str, ex: int | None = None):
        raise redis.RedisError(f"boom writing {key}")

    def publish(self, channel: str, payload: str):
        raise redis.RedisError(f"boom publishing {channel}")


def test_cache_falls_back_to_memory_when_redis_get_fails():
    cache = CacheService(Settings(use_redis=False))
    cache.set_json("config:prod:checkout-service.timeout:version:1", {"version": 1})
    cache.client = FailingRedisClient()

    payload = cache.get_json("config:prod:checkout-service.timeout:version:1")

    assert payload == {"version": 1}
    assert cache.is_available() is False


def test_cache_preserves_memory_write_when_redis_set_fails():
    cache = CacheService(Settings(use_redis=False))
    cache.client = FailingRedisClient()

    cache.set_json("config:prod:checkout-service.timeout:version:2", {"version": 2})

    assert cache.get_json("config:prod:checkout-service.timeout:version:2") == {"version": 2}


def test_cache_publish_degrades_gracefully_when_redis_publish_fails():
    cache = CacheService(Settings(use_redis=False))
    cache.client = FailingRedisClient()

    cache.publish("config-events", {"event": "rollout_started"})

    assert cache.is_available() is False
