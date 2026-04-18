from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.core.settings import Settings
from app.services.config_service import ConfigService


def build_service() -> ConfigService:
    return ConfigService(
        settings=Settings(use_redis=False),
        database=Mock(),
        cache=Mock(),
        notifications=Mock(),
    )


SERVICE = build_service()
DETERMINISM_CLIENTS = [f"deterministic-client-{index:03d}" for index in range(64)]
MONOTONIC_CLIENTS = [f"monotonic-client-{index:03d}" for index in range(80)]
FULL_ROLLOUT_CLIENTS = [f"full-rollout-client-{index:03d}" for index in range(32)]


@pytest.mark.parametrize("percent", [1, 5, 10, 25])
@pytest.mark.parametrize("client_id", DETERMINISM_CLIENTS)
def test_canary_bucketing_is_deterministic_across_sample_clients(client_id, percent):
    first = SERVICE._is_canary_client("payment-service.flags", "prod", "payment-service", client_id, percent)  # noqa: SLF001
    second = SERVICE._is_canary_client("payment-service.flags", "prod", "payment-service", client_id, percent)  # noqa: SLF001

    assert first is second


@pytest.mark.parametrize("client_id", MONOTONIC_CLIENTS)
def test_canary_exposure_is_monotonic_as_rollout_percentage_increases(client_id):
    exposure = [
        SERVICE._is_canary_client("payment-service.flags", "prod", "payment-service", client_id, percent)  # noqa: SLF001
        for percent in (1, 5, 10, 25, 50, 100)
    ]

    assert exposure == sorted(exposure)


@pytest.mark.parametrize("client_id", FULL_ROLLOUT_CLIENTS)
def test_full_rollout_always_places_client_in_canary_bucket(client_id):
    assert SERVICE._is_canary_client("payment-service.flags", "prod", "payment-service", client_id, 100) is True  # noqa: SLF001
