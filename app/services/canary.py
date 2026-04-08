from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from app.core.metrics import ROLLOUT_EVALUATIONS
from app.db.models import Rollout
from app.db.session import Database
from app.services.cache import CacheService
from app.services.config_service import ConfigService

logger = logging.getLogger(__name__)


class CanaryMonitor:
    def __init__(
        self,
        *,
        database: Database,
        config_service: ConfigService,
        cache: CacheService,
        poll_interval_seconds: float,
    ) -> None:
        self.database = database
        self.config_service = config_service
        self.cache = cache
        self.poll_interval_seconds = poll_interval_seconds
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()

    async def start(self) -> None:
        if self._task is None:
            self._stop.clear()
            self._task = asyncio.create_task(self._run(), name="canary-monitor")

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            await self._task
            self._task = None

    async def _run(self) -> None:
        while not self._stop.is_set():
            try:
                await self._evaluate_rollouts()
            except Exception as exc:
                logger.exception("canary monitor iteration failed: %s", exc)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.poll_interval_seconds)
            except TimeoutError:
                continue

    async def _evaluate_rollouts(self) -> None:
        active_rollouts = self.config_service.get_active_rollouts()
        now = datetime.now(timezone.utc)
        for rollout in active_rollouts:
            created_at = rollout.created_at
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            if rollout.canary_metric and rollout.canary_threshold is not None:
                current = self.config_service.get_metric_value(rollout.target, rollout.canary_metric)
                if current is not None and current > rollout.canary_threshold:
                    ROLLOUT_EVALUATIONS.labels("threshold_breach", rollout.environment).inc()
                    logger.warning(
                        "rollout canary threshold breached",
                        extra={
                            "event": "rollout.canary_breach",
                            "context": {
                                "config_name": rollout.config_name,
                                "environment": rollout.environment,
                                "target": rollout.target,
                                "rollout_id": rollout.rollout_id,
                                "metric": rollout.canary_metric,
                                "threshold": rollout.canary_threshold,
                                "value": current,
                            },
                        },
                    )
                    await self.config_service.auto_rollback_rollout(
                        rollout.rollout_id,
                        reason=(
                            f"canary metric '{rollout.canary_metric}' "
                            f"breached threshold {rollout.canary_threshold:.4f} with value {current:.4f}"
                        ),
                    )
                    continue
                if current is None:
                    ROLLOUT_EVALUATIONS.labels("no_signal", rollout.environment).inc()
                else:
                    ROLLOUT_EVALUATIONS.labels("healthy_signal", rollout.environment).inc()
            if rollout.canary_window_minutes is not None:
                deadline = created_at + timedelta(minutes=rollout.canary_window_minutes)
                if now >= deadline:
                    ROLLOUT_EVALUATIONS.labels("auto_promote", rollout.environment).inc()
                    logger.info(
                        "rollout canary window elapsed",
                        extra={
                            "event": "rollout.auto_promote",
                            "context": {
                                "config_name": rollout.config_name,
                                "environment": rollout.environment,
                                "target": rollout.target,
                                "rollout_id": rollout.rollout_id,
                            },
                        },
                    )
                    await self.config_service.promote_rollout(rollout.rollout_id)
                    continue
            ROLLOUT_EVALUATIONS.labels("evaluated", rollout.environment).inc()
