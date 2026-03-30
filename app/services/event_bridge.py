from __future__ import annotations

import asyncio
import json
import logging

import redis

from app.core.settings import Settings
from app.services.cache import CacheService
from app.services.notifications import NotificationHub

logger = logging.getLogger(__name__)


class RedisEventBridge:
    def __init__(self, *, settings: Settings, cache: CacheService, notifications: NotificationHub) -> None:
        self.settings = settings
        self.cache = cache
        self.notifications = notifications
        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is None and self.cache.client is not None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="redis-event-bridge")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            pubsub = None
            try:
                pubsub = self.cache.client.pubsub() if self.cache.client else None
                if pubsub is None:
                    return
                await asyncio.to_thread(pubsub.subscribe, self.settings.notification_channel)
                self.cache._set_available(True)  # noqa: SLF001
                while not self._stop.is_set():
                    message = await asyncio.to_thread(
                        pubsub.get_message,
                        ignore_subscribe_messages=True,
                        timeout=1.0,
                    )
                    if not message:
                        continue
                    payload = json.loads(message["data"])
                    if payload.get("source_instance") == self.settings.instance_id:
                        continue
                    await self.notifications.publish(payload)
            except redis.RedisError as exc:
                logger.warning("redis event bridge disconnected: %s", exc)
                self.cache._set_available(False)  # noqa: SLF001
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=1.0)
                except TimeoutError:
                    continue
            finally:
                if pubsub is not None:
                    await asyncio.to_thread(pubsub.close)
