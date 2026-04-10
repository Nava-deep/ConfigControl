from __future__ import annotations

import redis
from fastapi.testclient import TestClient

from app.core.settings import Settings
from app.main import create_app

ADMIN_HEADERS = {"X-User-Id": "alice", "X-Role": "admin"}
READER_HEADERS = {"X-User-Id": "reader", "X-Role": "reader"}

SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
    "required": ["timeout_ms"],
    "additionalProperties": False,
}


class PublishFailRedisClient:
    def get(self, key: str):
        return None

    def set(self, key: str, value: str, ex: int | None = None):
        return True

    def publish(self, channel: str, payload: str):
        raise redis.RedisError(f"boom publishing {channel}")


class ReadFailRedisClient(PublishFailRedisClient):
    def get(self, key: str):
        raise redis.RedisError(f"boom reading {key}")


def create_version(client, value: int, *, environment: str = "prod"):
    return client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "environment": environment,
            "schema": SCHEMA,
            "value": {"timeout_ms": value},
        },
    )


def test_service_starts_and_serves_configs_when_redis_is_unavailable_at_startup(tmp_path):
    app = create_app(
        Settings(
            database_url=f"sqlite:///{tmp_path / 'redis-outage.db'}",
            redis_url="redis://127.0.0.1:1/0",
            use_redis=True,
            sdk_cache_dir=tmp_path / ".sdk-cache",
            canary_poll_interval_seconds=0.1,
            longpoll_timeout_seconds=1,
        )
    )

    with TestClient(app) as client:
        ready = client.get("/health/ready")
        assert ready.status_code == 200
        assert ready.json()["database"] is True
        assert ready.json()["redis"] is False

        created = create_version(client, 2000)
        assert created.status_code == 201, created.text

        resolved = client.get("/configs/checkout-service.timeout", headers=READER_HEADERS)
        assert resolved.status_code == 200
        assert resolved.json()["value"]["timeout_ms"] == 2000

        metrics = client.get("/metrics")
        assert metrics.status_code == 200
        assert 'config_service_redis_fallback_total{operation="startup"}' in metrics.text


def test_invalid_schema_definition_is_rejected(client):
    response = client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "environment": "prod",
            "schema": {"type": "definitely-not-a-valid-jsonschema-type"},
            "value": {"timeout_ms": 1000},
        },
    )
    assert response.status_code == 422


def test_websocket_delivery_continues_when_redis_publish_fails(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2800).status_code == 201

    container = client.app.state.container
    container.cache.client = PublishFailRedisClient()
    container.cache._set_available(True)  # noqa: SLF001

    with client.websocket_connect(
        "/watch/ws?config_name=checkout-service.timeout&environment=prod&target=checkout-service",
        headers=READER_HEADERS,
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["event"] == "connected"

        rollout = client.post(
            "/configs/checkout-service.timeout/rollout",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "percent": 10},
        )
        assert rollout.status_code == 200, rollout.text

        event = websocket.receive_json()
        assert event["event"] == "rollout_started"
        assert event["target"] == "checkout-service"

    assert container.cache.is_available() is False


def test_longpoll_delivery_continues_when_redis_publish_fails(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 3200).status_code == 201

    container = client.app.state.container
    container.cache.client = PublishFailRedisClient()
    container.cache._set_available(True)  # noqa: SLF001
    last_sequence = container.notifications._sequence  # noqa: SLF001

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text

    event = client.get(
        "/watch/longpoll",
        headers=READER_HEADERS,
        params={
            "last_sequence": last_sequence,
            "config_name": "checkout-service.timeout",
            "environment": "prod",
            "target": "checkout-service",
            "timeout": 0.1,
        },
    )
    assert event.status_code == 200, event.text
    assert event.json()["event"] == "rollout_started"
    assert container.cache.is_available() is False


def test_latest_read_uses_memory_fallback_when_redis_get_fails(client):
    assert create_version(client, 2000).status_code == 201

    container = client.app.state.container
    container.cache.client = ReadFailRedisClient()
    container.cache._set_available(True)  # noqa: SLF001

    response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "latest", "environment": "prod"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["value"]["timeout_ms"] == 2000
    assert container.cache.is_available() is False


def test_metrics_record_publish_fallback_after_redis_publish_failure(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 3200).status_code == 201

    container = client.app.state.container
    container.cache.client = PublishFailRedisClient()
    container.cache._set_available(True)  # noqa: SLF001

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    assert 'config_service_redis_fallback_total{operation="publish"}' in metrics.text


def test_rollback_succeeds_when_redis_publish_fails(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 3600).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text
    assert rollout.json()["status"] == "promoted"

    container = client.app.state.container
    container.cache.client = PublishFailRedisClient()
    container.cache._set_available(True)  # noqa: SLF001

    rollback = client.post(
        "/configs/checkout-service.timeout/rollback",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "target_version": 1},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["status"] == "rolled_back"
    assert container.cache.is_available() is False

    resolved = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod", "target": "checkout-service", "client_id": "rollback-check"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 1
    assert resolved.json()["source"] == "stable"
