from __future__ import annotations

import time

import pytest

from app.db.models import ConfigAssignment

ADMIN_HEADERS = {"X-User-Id": "alice", "X-Role": "admin"}
READER_HEADERS = {"X-User-Id": "reader", "X-Role": "reader"}


SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
    "required": ["timeout_ms"],
    "additionalProperties": False,
}


def create_version(client, value: int, description: str | None = None):
    return client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "schema": SCHEMA,
            "value": {"timeout_ms": value},
            "description": description,
        },
    )


def find_client_with_source(client, source: str) -> str:
    for index in range(500):
        client_id = f"client-{index}"
        response = client.get(
            "/configs/checkout-service.timeout",
            headers=READER_HEADERS,
            params={"version": "resolved", "target": "checkout-service", "client_id": client_id},
        )
        if response.json()["source"] == source:
            return client_id
    raise AssertionError(f"unable to find client with source={source}")


def test_create_resolve_and_version_history(client):
    first = create_version(client, 2000, "baseline")
    assert first.status_code == 201, first.text
    assert first.json()["activated"] is True

    second = create_version(client, 3000, "candidate")
    assert second.status_code == 201, second.text
    assert second.json()["activated"] is False

    resolved = client.get("/configs/checkout-service.timeout", headers=READER_HEADERS)
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 1
    assert resolved.json()["source"] == "stable"

    latest = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "latest"},
    )
    assert latest.status_code == 200
    assert latest.json()["version"] == 2
    assert latest.json()["value"]["timeout_ms"] == 3000

    versions = client.get("/configs/checkout-service.timeout/versions", headers=READER_HEADERS)
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [2, 1]


def test_reader_cannot_mutate_configs(client):
    response = client.post(
        "/configs",
        headers=READER_HEADERS,
        json={"name": "checkout-service.timeout", "schema": SCHEMA, "value": {"timeout_ms": 1000}},
    )
    assert response.status_code == 403


def test_websocket_receives_rollout_event_and_canary_resolution(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2500).status_code == 201

    with client.websocket_connect(
        "/watch/ws?config_name=checkout-service.timeout&target=checkout-service",
        headers=READER_HEADERS,
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["event"] == "connected"

        rollout = client.post(
            "/configs/checkout-service.timeout/rollout",
            headers=ADMIN_HEADERS,
            json={
                "target": "checkout-service",
                "percent": 10,
                "canary_check": {"metric": "error_rate", "threshold": 0.05, "window": 5},
            },
        )
        assert rollout.status_code == 200, rollout.text

        event = websocket.receive_json()
        assert event["event"] == "rollout_started"
        assert event["version"] == 2
        assert event["rollout_percent"] == 10

    canary_client = find_client_with_source(client, "canary")
    stable_client = find_client_with_source(client, "stable")

    canary = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "target": "checkout-service", "client_id": canary_client},
    )
    stable = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "target": "checkout-service", "client_id": stable_client},
    )
    assert canary.json()["version"] == 2
    assert stable.json()["version"] == 1


def test_canary_metric_breach_triggers_auto_rollback(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 4000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={
            "target": "checkout-service",
            "percent": 20,
            "canary_check": {"metric": "error_rate", "threshold": 0.01, "window": 5},
        },
    )
    assert rollout.status_code == 200, rollout.text

    metric = client.post(
        "/simulation/metrics",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "metric": "error_rate", "value": 0.02},
    )
    assert metric.status_code == 200

    canary_client = find_client_with_source(client, "canary")
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        resolved = client.get(
            "/configs/checkout-service.timeout",
            headers=READER_HEADERS,
            params={"version": "resolved", "target": "checkout-service", "client_id": canary_client},
        )
        if resolved.json()["version"] == 1 and resolved.json()["source"] == "stable":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("rollout did not rollback within timeout")

    audit = client.get("/audit", headers=ADMIN_HEADERS, params={"name": "checkout-service.timeout"})
    assert audit.status_code == 200
    assert any(item["action"] == "config.rollout.auto_rollback" for item in audit.json())


def test_ready_health_returns_503_on_database_failure(client):
    def fail_ping():
        raise RuntimeError("db unavailable")

    client.app.state.container.database.ping = fail_ping
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["status"] == "degraded"


def test_reader_watchers_must_scope_their_subscription(client):
    with pytest.raises(Exception):
        with client.websocket_connect("/watch/ws", headers=READER_HEADERS):
            pass

    response = client.get("/watch/longpoll", headers=READER_HEADERS)
    assert response.status_code == 403


def test_resolved_get_does_not_create_assignment_rows(client):
    assert create_version(client, 2000).status_code == 201
    container = client.app.state.container
    with container.database.session() as session:
        before = session.query(ConfigAssignment).count()

    response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "target": "payments-service", "client_id": "reader-a"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1

    with container.database.session() as session:
        after = session.query(ConfigAssignment).count()

    assert before == after == 1


def test_partial_rollout_can_be_promoted_manually(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 5000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["rollout_id"]

    promoted = client.post(
        f"/configs/checkout-service.timeout/rollouts/{rollout_id}/promote",
        headers=ADMIN_HEADERS,
    )
    assert promoted.status_code == 200, promoted.text
    assert promoted.json()["status"] == "promoted"

    resolved = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "target": "checkout-service", "client_id": "client-999"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 2
    assert resolved.json()["source"] == "stable"


def test_invalid_version_query_returns_422(client):
    assert create_version(client, 2000).status_code == 201
    response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "not-a-version"},
    )
    assert response.status_code == 422
