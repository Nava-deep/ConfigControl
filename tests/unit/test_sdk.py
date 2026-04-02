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


def test_sdk_reports_anonymous_failure_payload(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-d",
        target="checkout-service",
        ttl_seconds=30,
        cache_dir=tmp_path,
    )
    client._last_seen_payloads["checkout-service.timeout"] = {  # noqa: SLF001
        "version": 2,
        "source": "canary",
    }

    captured: dict = {}

    class DummyResponse:
        def raise_for_status(self):
            return None

    def fake_post(url, json=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["timeout"] = timeout
        return DummyResponse()

    monkeypatch.setattr(client._client, "post", fake_post)  # noqa: SLF001

    try:
        raise RuntimeError("boom")
    except RuntimeError as exc:
        client.report_failure(
            "checkout-service.timeout",
            exc,
            source="demo-client",
            app_version="demo-client",
            metadata={"safe_note": "kept", "stack_trace": "drop-me"},
        )

    assert captured["url"] == "/telemetry/failures"
    assert captured["timeout"] == 2.0
    assert captured["json"]["target"] == "checkout-service"
    assert captured["json"]["config_version"] == 2
    assert captured["json"]["config_source"] == "canary"
    assert captured["json"]["anonymous_installation_id"]
    assert captured["json"]["anonymous_installation_id"] != "client-d"
    assert captured["json"]["metadata"]["safe_note"] == "kept"
    assert "stack_trace" in captured["json"]["metadata"]
    assert len(captured["json"]["fingerprint"]) == 32
    client.close()
