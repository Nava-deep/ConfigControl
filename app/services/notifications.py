from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from fastapi import WebSocket

from app.core.metrics import ACTIVE_WEBSOCKETS


@dataclass
class Subscription:
    websocket: WebSocket
    config_name: str | None
    target: str | None


class NotificationHub:
    def __init__(self):
        self._subscriptions: dict[int, Subscription] = {}
        self._events: deque[dict[str, Any]] = deque(maxlen=512)
        self._condition = asyncio.Condition()
        self._sequence = 0

    async def register(self, websocket: WebSocket, config_name: str | None, target: str | None) -> None:
        await websocket.accept()
        self._subscriptions[id(websocket)] = Subscription(websocket, config_name, target)
        ACTIVE_WEBSOCKETS.inc()
        await websocket.send_json(
            {
                "event": "connected",
                "sequence": self._sequence,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def unregister(self, websocket: WebSocket) -> None:
        if self._subscriptions.pop(id(websocket), None) is not None:
            ACTIVE_WEBSOCKETS.dec()

    async def publish(self, payload: dict[str, Any]) -> dict[str, Any]:
        async with self._condition:
            self._sequence += 1
            event = {
                **payload,
                "sequence": self._sequence,
                "timestamp": payload.get("timestamp") or datetime.now(timezone.utc).isoformat(),
            }
            self._events.append(event)
            self._condition.notify_all()
        stale: list[Subscription] = []
        for subscription in list(self._subscriptions.values()):
            if not self._matches(event, subscription.config_name, subscription.target):
                continue
            try:
                await subscription.websocket.send_json(event)
            except Exception:
                stale.append(subscription)
        for subscription in stale:
            await self.unregister(subscription.websocket)
        return event

    async def poll(
        self,
        last_sequence: int,
        config_name: str | None = None,
        target: str | None = None,
        timeout: float = 25.0,
    ) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while True:
            for event in self._events:
                if event["sequence"] > last_sequence and self._matches(event, config_name, target):
                    return event
            remaining = deadline - loop.time()
            if remaining <= 0:
                return None
            try:
                async with self._condition:
                    await asyncio.wait_for(self._condition.wait(), timeout=remaining)
            except TimeoutError:
                return None

    @staticmethod
    def _matches(event: dict[str, Any], config_name: str | None, target: str | None) -> bool:
        if config_name and event.get("config_name") != config_name:
            return False
        if target and event.get("target") != target:
            return False
        return True
