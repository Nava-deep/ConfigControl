from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from app.core.metrics import STARTUP_DEPENDENCY_STATUS
from app.core.settings import Settings, get_settings
from app.db.session import Database, build_database
from app.services.cache import CacheService
from app.services.canary import CanaryMonitor
from app.services.config_service import ConfigService
from app.services.event_bridge import RedisEventBridge
from app.services.notifications import NotificationHub
from app.services.telemetry import TelemetryService

logger = logging.getLogger(__name__)


@dataclass
class ServiceContainer:
    settings: Settings
    database: Database
    cache: CacheService
    notifications: NotificationHub
    config_service: ConfigService
    telemetry_service: TelemetryService
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
        telemetry_service = TelemetryService(
            settings=resolved_settings,
            database=database,
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
            telemetry_service=telemetry_service,
            canary_monitor=canary_monitor,
            event_bridge=event_bridge,
        )

    async def startup(self) -> None:
        self.database.create_all()
        database_ok = self.database.ping()
        STARTUP_DEPENDENCY_STATUS.labels("database").set(1 if database_ok else 0)
        if not database_ok:
            raise RuntimeError("database dependency check failed during startup")
        STARTUP_DEPENDENCY_STATUS.labels("redis").set(1 if self.cache.is_available() else 0)
        logger.info(
            "service startup checks complete",
            extra={
                "event": "startup.ready",
                "context": {
                    "database": database_ok,
                    "redis": self.cache.is_available(),
                    "instance_id": self.settings.instance_id,
                },
            },
        )
        await self.event_bridge.start()
        await self.canary_monitor.start()

    async def shutdown(self) -> None:
        await self.canary_monitor.stop()
        await self.event_bridge.stop()
        await asyncio.to_thread(self.database.dispose)
        logger.info("service shutdown complete", extra={"event": "startup.shutdown", "context": {"instance_id": self.settings.instance_id}})
