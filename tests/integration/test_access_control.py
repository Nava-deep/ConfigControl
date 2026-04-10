from __future__ import annotations

import pytest


SCHEMA = {
    "type": "object",
    "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
    "required": ["timeout_ms"],
    "additionalProperties": False,
}


def headers(role: str) -> dict[str, str]:
    return {"X-User-Id": f"{role}-user", "X-Role": role}


def create_version(client, role: str, value: int):
    return client.post(
        "/configs",
        headers=headers(role),
        json={
            "name": "checkout-service.timeout",
            "environment": "prod",
            "schema": SCHEMA,
            "value": {"timeout_ms": value},
        },
    )


def create_telemetry_event(client) -> None:
    response = client.post(
        "/telemetry/failures",
        headers=headers("reader"),
        json={
            "config_name": "checkout-service.timeout",
            "environment": "prod",
            "target": "checkout-service",
            "source": "demo-client",
            "error_type": "RuntimeError",
            "fingerprint": "0123456789abcdef0123456789abcdef",
            "anonymous_installation_id": "anon-installation-1234567890",
            "config_version": 1,
            "config_source": "stable",
            "metadata": {},
        },
    )
    assert response.status_code == 202, response.text


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [("admin", 201), ("operator", 201), ("reader", 403)],
)
def test_create_config_role_matrix(client, role, expected_status):
    response = create_version(client, role, 2000)

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [("admin", 200), ("operator", 200), ("reader", 403)],
)
def test_rollout_role_matrix(client, role, expected_status):
    assert create_version(client, "admin", 2000).status_code == 201
    assert create_version(client, "admin", 2500).status_code == 201

    response = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=headers(role),
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [("admin", 200), ("operator", 200), ("reader", 403)],
)
def test_simulation_metric_role_matrix(client, role, expected_status):
    response = client.post(
        "/simulation/metrics",
        headers=headers(role),
        json={"target": "checkout-service", "metric": "error_rate", "value": 0.02},
    )

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("role", "expected_status"),
    [("admin", 200), ("operator", 200), ("reader", 403)],
)
def test_failure_telemetry_list_role_matrix(client, role, expected_status):
    assert create_version(client, "admin", 2000).status_code == 201
    create_telemetry_event(client)

    response = client.get("/telemetry/failures", headers=headers(role))

    assert response.status_code == expected_status


@pytest.mark.parametrize(
    ("role", "params", "expected_status"),
    [
        ("reader", {}, 403),
        ("reader", {"config_name": "checkout-service.timeout"}, 204),
        ("operator", {}, 204),
    ],
)
def test_longpoll_access_rules(client, role, params, expected_status):
    response = client.get(
        "/watch/longpoll",
        headers=headers(role),
        params={"last_sequence": 0, "timeout": 0.01, **params},
    )

    assert response.status_code == expected_status
