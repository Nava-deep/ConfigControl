from __future__ import annotations

import time

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


class DelayedFailingRedisClient(FailingRedisClient):
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds

    def get(self, key: str):
        time.sleep(self.delay_seconds)
        raise redis.RedisError(f"delayed boom reading {key}")


def test_cache_falls_back_to_memory_when_redis_get_fails():
    cache = CacheService(Settings(use_redis=False))
    cache.set_json("config:prod:checkout-service.timeout:version:1", {"version": 1})
    cache.client = FailingRedisClient()

    payload = cache.get_json("config:prod:checkout-service.timeout:version:1")

    assert payload == {"version": 1}
    assert cache.is_available() is False


def test_cache_returns_none_on_memory_miss_without_redis():
    cache = CacheService(Settings(use_redis=False))

    payload = cache.get_json("config:prod:checkout-service.timeout:version:missing")

    assert payload is None
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


def test_cache_serves_memory_value_after_delayed_redis_get_failure():
    cache = CacheService(Settings(use_redis=False))
    cache.set_json("config:prod:checkout-service.timeout:version:3", {"version": 3})
    cache.client = DelayedFailingRedisClient(delay_seconds=0.02)

    started = time.perf_counter()
    payload = cache.get_json("config:prod:checkout-service.timeout:version:3")
    elapsed = time.perf_counter() - started

    assert payload == {"version": 3}
    assert elapsed >= 0.018
    assert cache.is_available() is False


def test_cache_metric_round_trip_uses_memory_store():
    cache = CacheService(Settings(use_redis=False))

    written = cache.set_metric("checkout-service", "error_rate", 0.02)
    fetched = cache.get_metric("checkout-service", "error_rate")

    assert written["target"] == "checkout-service"
    assert written["metric"] == "error_rate"
    assert fetched is not None
    assert fetched["value"] == 0.02
