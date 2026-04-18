from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services.canary import CanaryMonitor


def build_monitor() -> tuple[CanaryMonitor, Mock]:
    config_service = Mock()
    monitor = CanaryMonitor(
        database=Mock(),
        config_service=config_service,
        cache=Mock(),
        poll_interval_seconds=0.01,
    )
    return monitor, config_service


def rollout(
    *,
    metric: str | None = None,
    threshold: float | None = None,
    window_minutes: int | None = None,
    created_at: datetime | None = None,
    environment: str = "prod",
) -> SimpleNamespace:
    return SimpleNamespace(
        rollout_id="rollout-1",
        config_name="checkout-service.timeout",
        environment=environment,
        target="checkout-service",
        canary_metric=metric,
        canary_threshold=threshold,
        canary_window_minutes=window_minutes,
        created_at=created_at or datetime.now(timezone.utc),
    )


@pytest.mark.asyncio
async def test_canary_monitor_start_creates_one_task(monkeypatch):
    monitor, _config_service = build_monitor()

    async def fake_run():
        await monitor._stop.wait()  # noqa: SLF001

    monkeypatch.setattr(monitor, "_run", fake_run)

    await monitor.start()
    first_task = monitor._task  # noqa: SLF001
    await monitor.start()

    assert monitor._task is first_task  # noqa: SLF001

    await monitor.stop()


@pytest.mark.asyncio
async def test_canary_monitor_stop_clears_task(monkeypatch):
    monitor, _config_service = build_monitor()

    async def fake_run():
        await monitor._stop.wait()  # noqa: SLF001

    monkeypatch.setattr(monitor, "_run", fake_run)

    await monitor.start()
    assert monitor._task is not None  # noqa: SLF001

    await monitor.stop()

    assert monitor._task is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_canary_monitor_auto_rolls_back_on_threshold_breach():
    monitor, config_service = build_monitor()
    config_service.get_active_rollouts.return_value = [
        rollout(metric="error_rate", threshold=0.01, window_minutes=5)
    ]
    config_service.get_metric_value.return_value = 0.03
    config_service.auto_rollback_rollout = AsyncMock()
    config_service.promote_rollout = AsyncMock()

    await monitor._evaluate_rollouts()

    config_service.auto_rollback_rollout.assert_awaited_once()
    config_service.promote_rollout.assert_not_awaited()


@pytest.mark.asyncio
async def test_canary_monitor_does_not_act_when_metric_missing():
    monitor, config_service = build_monitor()
    config_service.get_active_rollouts.return_value = [
        rollout(metric="error_rate", threshold=0.01, window_minutes=5)
    ]
    config_service.get_metric_value.return_value = None
    config_service.auto_rollback_rollout = AsyncMock()
    config_service.promote_rollout = AsyncMock()

    await monitor._evaluate_rollouts()

    config_service.auto_rollback_rollout.assert_not_awaited()
    config_service.promote_rollout.assert_not_awaited()


@pytest.mark.asyncio
async def test_canary_monitor_does_not_act_when_metric_healthy_before_deadline():
    monitor, config_service = build_monitor()
    config_service.get_active_rollouts.return_value = [
        rollout(metric="error_rate", threshold=0.05, window_minutes=5, created_at=datetime.now(timezone.utc))
    ]
    config_service.get_metric_value.return_value = 0.01
    config_service.auto_rollback_rollout = AsyncMock()
    config_service.promote_rollout = AsyncMock()

    await monitor._evaluate_rollouts()

    config_service.auto_rollback_rollout.assert_not_awaited()
    config_service.promote_rollout.assert_not_awaited()


@pytest.mark.asyncio
async def test_canary_monitor_auto_promotes_after_window():
    monitor, config_service = build_monitor()
    config_service.get_active_rollouts.return_value = [
        rollout(
            metric="error_rate",
            threshold=0.05,
            window_minutes=1,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        )
    ]
    config_service.get_metric_value.return_value = 0.01
    config_service.auto_rollback_rollout = AsyncMock()
    config_service.promote_rollout = AsyncMock()

    await monitor._evaluate_rollouts()

    config_service.promote_rollout.assert_awaited_once_with("rollout-1")
    config_service.auto_rollback_rollout.assert_not_awaited()


@pytest.mark.asyncio
async def test_canary_monitor_handles_naive_datetime_for_auto_promotion():
    monitor, config_service = build_monitor()
    config_service.get_active_rollouts.return_value = [
        rollout(
            metric=None,
            threshold=None,
            window_minutes=1,
            created_at=(datetime.now(timezone.utc) - timedelta(minutes=2)).replace(tzinfo=None),
        )
    ]
    config_service.auto_rollback_rollout = AsyncMock()
    config_service.promote_rollout = AsyncMock()

    await monitor._evaluate_rollouts()

    config_service.promote_rollout.assert_awaited_once_with("rollout-1")
