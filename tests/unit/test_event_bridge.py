from __future__ import annotations

import json

import pytest

from app.core.settings import Settings
from app.services.cache import CacheService
from app.services.event_bridge import RedisEventBridge
from app.services.notifications import NotificationHub


class FakePubSub:
    def __init__(self, messages: list[dict[str, str] | None], *, on_exhausted=None) -> None:
        self.messages = messages
        self.closed = False
        self.subscribed_channel: str | None = None
        self.on_exhausted = on_exhausted

    def subscribe(self, channel: str) -> None:
        self.subscribed_channel = channel

    def get_message(self, ignore_subscribe_messages: bool = True, timeout: float = 1.0):
        if not self.messages:
            if self.on_exhausted is not None:
                self.on_exhausted()
            return None
        return self.messages.pop(0)

    def close(self) -> None:
        self.closed = True


class FakeRedisClient:
    def __init__(self, pubsub: FakePubSub) -> None:
        self._pubsub = pubsub

    def pubsub(self) -> FakePubSub:
        return self._pubsub


@pytest.mark.asyncio
async def test_event_bridge_start_is_noop_without_redis_client():
    settings = Settings(use_redis=False, instance_id="local-instance")
    cache = CacheService(settings)
    notifications = NotificationHub()
    bridge = RedisEventBridge(settings=settings, cache=cache, notifications=notifications)

    await bridge.start()

    assert bridge._task is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_event_bridge_forwards_external_instance_messages(monkeypatch):
    pubsub = FakePubSub(
        [
            {
                "data": json.dumps(
                    {
                        "event": "rollout_started",
                        "config_name": "checkout-service.timeout",
                        "environment": "prod",
                        "target": "checkout-service",
                        "source_instance": "remote-instance",
                    }
                )
            },
            None,
        ]
    )
    settings = Settings(use_redis=False, instance_id="local-instance")
    cache = CacheService(settings)
    cache.client = FakeRedisClient(pubsub)
    notifications = NotificationHub()
    forwarded: list[dict[str, str]] = []

    async def fake_publish(payload):
        forwarded.append(payload)
        bridge._stop.set()
        return payload

    monkeypatch.setattr(notifications, "publish", fake_publish)
    bridge = RedisEventBridge(settings=settings, cache=cache, notifications=notifications)

    await bridge._run()

    assert pubsub.subscribed_channel == settings.notification_channel
    assert pubsub.closed is True
    assert len(forwarded) == 1
    assert forwarded[0]["config_name"] == "checkout-service.timeout"
    assert forwarded[0]["source_instance"] == "remote-instance"


@pytest.mark.asyncio
async def test_event_bridge_ignores_same_instance_messages(monkeypatch):
    stop_called = False

    def mark_exhausted() -> None:
        nonlocal stop_called
        stop_called = True
        bridge._stop.set()

    pubsub = FakePubSub(
        [
            {
                "data": json.dumps(
                    {
                        "event": "rollout_started",
                        "config_name": "checkout-service.timeout",
                        "environment": "prod",
                        "target": "checkout-service",
                        "source_instance": "local-instance",
                    }
                )
            },
            None,
        ],
        on_exhausted=mark_exhausted,
    )
    settings = Settings(use_redis=False, instance_id="local-instance")
    cache = CacheService(settings)
    cache.client = FakeRedisClient(pubsub)
    notifications = NotificationHub()

    async def fake_publish(payload):
        raise AssertionError("same-instance pubsub messages should not be re-published")

    monkeypatch.setattr(notifications, "publish", fake_publish)
    bridge = RedisEventBridge(settings=settings, cache=cache, notifications=notifications)

    await bridge._run()

    assert pubsub.closed is True
    assert stop_called is True


@pytest.mark.asyncio
async def test_event_bridge_stop_clears_running_task(monkeypatch):
    settings = Settings(use_redis=False, instance_id="local-instance")
    cache = CacheService(settings)
    notifications = NotificationHub()
    bridge = RedisEventBridge(settings=settings, cache=cache, notifications=notifications)
    cache.client = FakeRedisClient(FakePubSub([]))

    async def fake_run():
        await bridge._stop.wait()  # noqa: SLF001

    monkeypatch.setattr(bridge, "_run", fake_run)

    await bridge.start()
    assert bridge._task is not None  # noqa: SLF001

    await bridge.stop()

    assert bridge._task is None  # noqa: SLF001
