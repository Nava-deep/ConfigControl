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


def test_sdk_uses_fresh_cache_without_hitting_network(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-cache",
        target="checkout-service",
        ttl_seconds=30,
        cache_dir=tmp_path,
    )
    client._save_cache(  # noqa: SLF001
        "checkout-service.timeout",
        "resolved",
        {
            "name": "checkout-service.timeout",
            "version": 3,
            "target": "checkout-service",
            "source": "stable",
            "value": {"timeout_ms": 1700},
            "schema": {},
            "description": None,
            "created_at": "2026-03-30T00:00:00+00:00",
        },
    )

    def explode(*args, **kwargs):
        raise AssertionError("fresh cache should avoid a network request")

    monkeypatch.setattr(client._client, "get", explode)  # noqa: SLF001
    config = client.get_typed("checkout-service.timeout", TimeoutConfig)
    assert config.timeout_ms == 1700
    client.close()


def test_sdk_refreshes_stale_cache_from_network(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-refresh",
        target="checkout-service",
        ttl_seconds=0,
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

    class DummyResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "name": "checkout-service.timeout",
                "version": 2,
                "target": "checkout-service",
                "source": "stable",
                "value": {"timeout_ms": 2500},
                "schema": {},
                "description": None,
                "created_at": "2026-03-30T00:00:00+00:00",
            }

    monkeypatch.setattr(client._client, "get", lambda *args, **kwargs: DummyResponse())  # noqa: SLF001
    config = client.get_typed("checkout-service.timeout", TimeoutConfig)
    assert config.timeout_ms == 2500
    client.close()


def test_sdk_reports_anonymous_failure_payload(tmp_path, monkeypatch):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-d",
        target="checkout-service",
        environment="staging",
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
    assert captured["json"]["environment"] == "staging"
    assert captured["json"]["config_version"] == 2
    assert captured["json"]["config_source"] == "canary"
    assert captured["json"]["anonymous_installation_id"]
    assert captured["json"]["anonymous_installation_id"] != "client-d"
    assert captured["json"]["metadata"]["safe_note"] == "kept"
    assert "stack_trace" in captured["json"]["metadata"]
    assert len(captured["json"]["fingerprint"]) == 32
    client.close()


def test_sdk_sanitizes_failure_metadata_and_limits_fields(tmp_path):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-sanitize",
        target="checkout-service",
        cache_dir=tmp_path,
    )

    sanitized = client._sanitize_failure_metadata(  # noqa: SLF001
        {
            "short_text": "ok",
            "long_text": "x" * 150,
            "numeric": 4,
            "boolean": True,
            "none_value": None,
            "list_value": [1, 2, 3],
            "dict_value": {"bad": "drop"},
            "extra_1": "a",
            "extra_2": "b",
            "extra_3": "c",
        }
    )

    assert sanitized["short_text"] == "ok"
    assert sanitized["long_text"] == "x" * 120
    assert sanitized["numeric"] == 4
    assert sanitized["boolean"] is True
    assert sanitized["none_value"] is None
    assert "list_value" not in sanitized
    assert "dict_value" not in sanitized
    assert set(sanitized) == {"short_text", "long_text", "numeric", "boolean", "none_value", "extra_1"}
    client.close()


def test_sdk_installation_id_is_reused_across_client_instances(tmp_path):
    first = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-install-a",
        target="checkout-service",
        cache_dir=tmp_path,
    )
    second = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-install-b",
        target="checkout-service",
        cache_dir=tmp_path,
    )

    assert first._anonymous_installation_id == second._anonymous_installation_id  # noqa: SLF001
    first.close()
    second.close()


def test_sdk_cache_is_scoped_by_environment(tmp_path):
    prod = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-e",
        target="checkout-service",
        environment="prod",
        cache_dir=tmp_path,
    )
    staging = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-e",
        target="checkout-service",
        environment="staging",
        cache_dir=tmp_path,
    )

    assert prod._cache_path("checkout-service.timeout", "resolved") != staging._cache_path(  # noqa: SLF001
        "checkout-service.timeout",
        "resolved",
    )
    prod.close()
    staging.close()


def test_sdk_websocket_url_includes_environment(tmp_path):
    client = ConfigClient[TimeoutConfig](
        base_url="http://config-service.local",
        client_id="client-f",
        target="checkout-service",
        environment="staging",
        cache_dir=tmp_path,
    )

    url = client._build_ws_url("checkout-service.timeout")  # noqa: SLF001
    assert "environment=staging" in url
    assert "config_name=checkout-service.timeout" in url
    client.close()
