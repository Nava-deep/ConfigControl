from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.core.settings import Settings, get_settings
from app.db.session import Database, build_database
from app.services.cache import CacheService
from app.services.canary import CanaryMonitor
from app.services.config_service import ConfigService
from app.services.event_bridge import RedisEventBridge
from app.services.notifications import NotificationHub


@dataclass
class ServiceContainer:
    settings: Settings
    database: Database
    cache: CacheService
    notifications: NotificationHub
    config_service: ConfigService
    canary_monitor: CanaryMonitor
    event_bridge: RedisEventBridge

    @classmethod
    def build(cls, settings: Settings | None = None) -> "ServiceContainer":
        resolved_settings = settings or get_settings()
        database = build_database(resolved_settings)
        cache = CacheService(resolved_settings)
        notifications = NotificationHub()
        config_service = ConfigService(
            settings=resolved_settings,
            database=database,
            cache=cache,
            notifications=notifications,
        )
        event_bridge = RedisEventBridge(
            settings=resolved_settings,
            cache=cache,
            notifications=notifications,
        )
        canary_monitor = CanaryMonitor(
            database=database,
            config_service=config_service,
            cache=cache,
            poll_interval_seconds=resolved_settings.canary_poll_interval_seconds,
        )
        return cls(
            settings=resolved_settings,
            database=database,
            cache=cache,
            notifications=notifications,
            config_service=config_service,
            canary_monitor=canary_monitor,
            event_bridge=event_bridge,
        )

    async def startup(self) -> None:
        self.database.create_all()
        await self.event_bridge.start()
        await self.canary_monitor.start()

    async def shutdown(self) -> None:
        await self.canary_monitor.stop()
        await self.event_bridge.stop()
        await asyncio.to_thread(self.database.dispose)
