from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import BaseModel

from app.sdk.client import ConfigClient


class TimeoutConfig(BaseModel):
    timeout_ms: int


def test_sdk_returns_cached_last_known_good_when_fetch_fails(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-a",
        target="checkout-service",
        ttl_seconds=30,
        cache_dir=tmp_path,
    )
    client._save_cache(  # noqa: SLF001
        "checkout-service.timeout",
        "resolved",
        {
            "name": "checkout-service.timeout",
            "version": 1,
            "target": "checkout-service",
            "source": "stable",
            "value": {"timeout_ms": 1500},
            "schema": {},
            "description": None,
            "created_at": "2026-03-30T00:00:00+00:00",
        },
    )

    def explode(*args, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(client._client, "get", explode)  # noqa: SLF001
    config = client.get_typed("checkout-service.timeout", TimeoutConfig, force_refresh=True)
    assert config.timeout_ms == 1500
    client.close()


def test_sdk_raises_without_cache_on_fetch_failure(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-b",
        target="checkout-service",
        ttl_seconds=30,
        cache_dir=tmp_path,
    )

    def explode(*args, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(client._client, "get", explode)  # noqa: SLF001
    with pytest.raises(RuntimeError):
        client.get_typed("checkout-service.timeout", TimeoutConfig, force_refresh=True)
    client.close()


def test_sdk_cache_is_scoped_by_requested_version(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-c",
        target="checkout-service",
        ttl_seconds=30,
        cache_dir=tmp_path,
    )
    client._save_cache(  # noqa: SLF001
        "checkout-service.timeout",
        "resolved",
        {
            "name": "checkout-service.timeout",
            "version": 1,
            "target": "checkout-service",
            "source": "stable",
            "value": {"timeout_ms": 1500},
            "schema": {},
            "description": None,
            "created_at": "2026-03-30T00:00:00+00:00",
        },
    )

    def explode(*args, **kwargs):
        raise RuntimeError("network unavailable")

    monkeypatch.setattr(client._client, "get", explode)  # noqa: SLF001
    with pytest.raises(RuntimeError):
        client.get_typed("checkout-service.timeout", TimeoutConfig, version="latest", force_refresh=True)
    client.close()
