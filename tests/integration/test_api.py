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


def create_version(
    client,
    value: int,
    description: str | None = None,
    *,
    environment: str = "prod",
    labels: dict[str, str] | None = None,
):
    return client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "environment": environment,
            "labels": labels or {},
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
            params={
                "version": "resolved",
                "environment": "prod",
                "target": "checkout-service",
                "client_id": client_id,
            },
        )
        if response.json()["source"] == source:
            return client_id
    raise AssertionError(f"unable to find client with source={source}")


def test_create_resolve_and_version_history(client):
    first = create_version(client, 2000, "baseline")
    assert first.status_code == 201, first.text
    assert first.json()["activated"] is True
    assert first.json()["environment"] == "prod"

    second = create_version(client, 3000, "candidate")
    assert second.status_code == 201, second.text
    assert second.json()["activated"] is False

    resolved = client.get("/configs/checkout-service.timeout", headers=READER_HEADERS)
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 1
    assert resolved.json()["source"] == "stable"
    assert resolved.json()["environment"] == "prod"

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


def test_first_version_requires_schema(client):
    response = client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "environment": "prod",
            "value": {"timeout_ms": 1000},
        },
    )
    assert response.status_code == 422
    assert "schema is required for the first config version" in response.text


def test_websocket_receives_rollout_event_and_canary_resolution(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2500).status_code == 201

    with client.websocket_connect(
        "/watch/ws?config_name=checkout-service.timeout&environment=prod&target=checkout-service",
        headers=READER_HEADERS,
    ) as websocket:
        connected = websocket.receive_json()
        assert connected["event"] == "connected"

        rollout = client.post(
            "/configs/checkout-service.timeout/rollout",
            headers=ADMIN_HEADERS,
            json={
                "target": "checkout-service",
                "environment": "prod",
                "percent": 10,
                "canary_check": {"metric": "error_rate", "threshold": 0.05, "window": 5},
            },
        )
        assert rollout.status_code == 200, rollout.text

        event = websocket.receive_json()
        assert event["event"] == "rollout_started"
        assert event["environment"] == "prod"
        assert event["version"] == 2
        assert event["rollout_percent"] == 10

    canary_client = find_client_with_source(client, "canary")
    stable_client = find_client_with_source(client, "stable")

    canary = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "environment": "prod", "target": "checkout-service", "client_id": canary_client},
    )
    stable = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "environment": "prod", "target": "checkout-service", "client_id": stable_client},
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
            "environment": "prod",
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
            params={"version": "resolved", "environment": "prod", "target": "checkout-service", "client_id": canary_client},
        )
        if resolved.json()["version"] == 1 and resolved.json()["source"] == "stable":
            break
        time.sleep(0.1)
    else:
        raise AssertionError("rollout did not rollback within timeout")

    audit = client.get(
        "/audit",
        headers=ADMIN_HEADERS,
        params={"name": "checkout-service.timeout", "environment": "prod"},
    )
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
        params={"version": "resolved", "environment": "prod", "target": "payments-service", "client_id": "reader-a"},
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
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
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
        params={"version": "resolved", "environment": "prod", "target": "checkout-service", "client_id": "client-999"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 2
    assert resolved.json()["source"] == "stable"


def test_active_rollout_can_be_advanced_from_one_to_ten_to_hundred_percent(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 5000).status_code == 201

    with client.websocket_connect(
        "/watch/ws?config_name=checkout-service.timeout&environment=prod&target=checkout-service",
        headers=READER_HEADERS,
    ) as websocket:
        websocket.receive_json()

        rollout = client.post(
            "/configs/checkout-service.timeout/rollout",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "percent": 1},
        )
        assert rollout.status_code == 200, rollout.text
        rollout_id = rollout.json()["rollout_id"]
        started_event = websocket.receive_json()
        assert started_event["event"] == "rollout_started"
        assert started_event["rollout_percent"] == 1

        advanced = client.post(
            f"/configs/checkout-service.timeout/rollouts/{rollout_id}/advance",
            headers=ADMIN_HEADERS,
            json={"percent": 10},
        )
        assert advanced.status_code == 200, advanced.text
        assert advanced.json()["percent"] == 10
        advanced_event = websocket.receive_json()
        assert advanced_event["event"] == "rollout_advanced"
        assert advanced_event["rollout_percent"] == 10

        promoted = client.post(
            f"/configs/checkout-service.timeout/rollouts/{rollout_id}/advance",
            headers=ADMIN_HEADERS,
            json={"percent": 100},
        )
        assert promoted.status_code == 200, promoted.text
        assert promoted.json()["status"] == "promoted"
        promoted_event = websocket.receive_json()
        assert promoted_event["event"] == "rollout_promoted"
        assert promoted_event["rollout_percent"] == 100

    resolved = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod", "target": "checkout-service", "client_id": "advanced-client"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 2
    assert resolved.json()["source"] == "stable"


def test_rollout_advance_rejects_equal_or_lower_percentage(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 5000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text
    rollout_id = rollout.json()["rollout_id"]

    response = client.post(
        f"/configs/checkout-service.timeout/rollouts/{rollout_id}/advance",
        headers=ADMIN_HEADERS,
        json={"percent": 10},
    )
    assert response.status_code == 409
    assert "greater than the current rollout percent" in response.text


def test_list_configs_respects_environment_filter_and_stable_version(client):
    assert create_version(client, 2000, environment="prod").status_code == 201
    assert create_version(client, 3500, environment="prod").status_code == 201
    assert create_version(client, 9000, environment="staging").status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text

    response = client.get("/configs", headers=READER_HEADERS, params={"environment": "prod"})
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["name"] == "checkout-service.timeout"
    assert body[0]["environment"] == "prod"
    assert body[0]["latest_version"] == 2
    assert body[0]["stable_target"] == "checkout-service"
    assert body[0]["stable_version"] == 2
    assert body[0]["updated_at"]


def test_invalid_version_query_returns_422(client):
    assert create_version(client, 2000).status_code == 201
    response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "not-a-version"},
    )
    assert response.status_code == 422


def test_anonymous_failure_telemetry_is_ingested_and_summarized(client):
    assert create_version(client, 2000).status_code == 201

    report = client.post(
        "/telemetry/failures",
        headers=READER_HEADERS,
        json={
            "config_name": "checkout-service.timeout",
            "target": "checkout-service",
            "source": "demo-client",
            "error_type": "RuntimeError",
            "fingerprint": "0123456789abcdef0123456789abcdef",
            "anonymous_installation_id": "anon-installation-1234567890",
            "config_version": 1,
            "config_source": "stable",
            "sdk_version": "0.1.0",
            "app_version": "demo-client",
            "runtime": "python-3.14.3",
            "metadata": {
                "simulate_failure_every": 3,
                "stack_trace": "should-be-dropped",
                "safe_note": "kept",
            },
        },
    )
    assert report.status_code == 202, report.text
    body = report.json()
    assert body["fingerprint"] == "0123456789abcdef0123456789abcdef"
    assert body["anonymous_installation_hash"] != "anon-installation-1234567890"

    events = client.get(
        "/telemetry/failures",
        headers=ADMIN_HEADERS,
        params={"config_name": "checkout-service.timeout"},
    )
    assert events.status_code == 200
    event = events.json()[0]
    assert event["error_type"] == "RuntimeError"
    assert event["metadata"]["safe_note"] == "kept"
    assert "stack_trace" not in event["metadata"]

    summary = client.get(
        "/telemetry/failures/summary",
        headers=ADMIN_HEADERS,
        params={"config_name": "checkout-service.timeout", "window_minutes": 60},
    )
    assert summary.status_code == 200
    top = summary.json()[0]
    assert top["event_count"] == 1
    assert top["distinct_installations"] == 1
    assert top["latest_config_version"] == 1


def test_environments_are_isolated_for_resolution_and_audit(client):
    assert create_version(client, 2000, environment="prod", labels={"team": "checkout"}).status_code == 201
    assert create_version(client, 9000, environment="staging", labels={"team": "checkout"}).status_code == 201

    prod = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod"},
    )
    staging = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "staging"},
    )

    assert prod.status_code == 200
    assert staging.status_code == 200
    assert prod.json()["value"]["timeout_ms"] == 2000
    assert staging.json()["value"]["timeout_ms"] == 9000
    assert staging.json()["labels"] == {"team": "checkout"}

    audit = client.get(
        "/audit",
        headers=ADMIN_HEADERS,
        params={"name": "checkout-service.timeout", "environment": "staging"},
    )
    assert audit.status_code == 200
    assert len(audit.json()) == 1
    assert audit.json()[0]["environment"] == "staging"


def test_diff_endpoint_returns_field_level_changes(client):
    schema = {
        "type": "object",
        "properties": {
            "timeout_ms": {"type": "integer", "minimum": 1},
            "retry_budget": {"type": "integer", "minimum": 0},
        },
        "required": ["timeout_ms"],
        "additionalProperties": False,
    }
    first = client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.routing",
            "environment": "prod",
            "schema": schema,
            "value": {"timeout_ms": 2000},
        },
    )
    assert first.status_code == 201, first.text
    second = client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.routing",
            "environment": "prod",
            "value": {"timeout_ms": 2500, "retry_budget": 3},
        },
    )
    assert second.status_code == 201, second.text

    diff = client.get(
        "/configs/checkout-service.routing/diff",
        headers=READER_HEADERS,
        params={"from_version": 1, "to_version": 2, "environment": "prod"},
    )
    assert diff.status_code == 200
    assert diff.json()["environment"] == "prod"
    assert {item["path"] for item in diff.json()["changes"]} == {"retry_budget", "timeout_ms"}


def test_dry_run_schema_migration_reports_incompatible_versions(client):
    assert create_version(client, 2000, environment="prod").status_code == 201
    assert create_version(client, 2500, environment="prod").status_code == 201

    response = client.post(
        "/configs/checkout-service.timeout/schema/dry-run",
        headers=ADMIN_HEADERS,
        json={
            "environment": "prod",
            "schema": {
                "type": "object",
                "properties": {
                    "timeout_ms": {"type": "integer", "minimum": 1},
                    "retry_budget": {"type": "integer", "minimum": 0},
                },
                "required": ["timeout_ms", "retry_budget"],
                "additionalProperties": False,
            },
            "value": {"timeout_ms": 2500, "retry_budget": 3},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["candidate_value_valid"] is True
    assert response.json()["current_versions_checked"] == 2
    assert response.json()["compatible_versions"] == []
    assert response.json()["incompatible_versions"] == [1, 2]
    assert "version 1" in response.json()["issues"][0]


def test_longpoll_returns_scoped_rollout_event(client):
    assert create_version(client, 2000, environment="staging").status_code == 201
    assert create_version(client, 3000, environment="staging").status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "staging", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text

    event = client.get(
        "/watch/longpoll",
        headers=READER_HEADERS,
        params={
            "last_sequence": 1,
            "config_name": "checkout-service.timeout",
            "environment": "staging",
            "target": "checkout-service",
            "timeout": 0.1,
        },
    )
    assert event.status_code == 200, event.text
    assert event.json()["event"] == "rollout_started"
    assert event.json()["environment"] == "staging"


def test_hundred_percent_rollout_promotes_immediately(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 6000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text
    assert rollout.json()["status"] == "promoted"

    resolved = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod", "target": "checkout-service", "client_id": "reader-a"},
    )
    assert resolved.status_code == 200
    assert resolved.json()["version"] == 2
    assert resolved.json()["source"] == "stable"


def test_hundred_percent_rollout_rejects_canary_check(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 6000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={
            "target": "checkout-service",
            "environment": "prod",
            "percent": 100,
            "canary_check": {"metric": "error_rate", "threshold": 0.05, "window": 5},
        },
    )
    assert rollout.status_code == 422
    assert "100% rollout cannot include canary_check" in rollout.text


def test_rollout_rejects_second_active_rollout_for_same_target(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2800).status_code == 201

    first = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert first.status_code == 200, first.text

    second = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 20},
    )
    assert second.status_code == 409
    assert "active rollout already exists" in second.text


def test_rollout_rejects_when_latest_version_is_already_stable(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2800).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text

    duplicate = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert duplicate.status_code == 409
    assert "latest version is already stable" in duplicate.text


def test_promote_non_active_rollout_returns_conflict(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 6000).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text

    promote = client.post(
        f"/configs/checkout-service.timeout/rollouts/{rollout.json()['rollout_id']}/promote",
        headers=ADMIN_HEADERS,
    )
    assert promote.status_code == 409


def test_reader_cannot_list_failure_telemetry(client):
    list_response = client.get("/telemetry/failures", headers=READER_HEADERS)
    summary_response = client.get("/telemetry/failures/summary", headers=READER_HEADERS)
    assert list_response.status_code == 403
    assert summary_response.status_code == 403


def test_canary_resolution_is_deterministic_for_same_client(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 3500).status_code == 201
    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 25},
    )
    assert rollout.status_code == 200, rollout.text

    first = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod", "target": "checkout-service", "client_id": "deterministic-client"},
    )
    second = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "prod", "target": "checkout-service", "client_id": "deterministic-client"},
    )
    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["source"] == second.json()["source"]
    assert first.json()["version"] == second.json()["version"]


def test_failure_telemetry_is_filtered_by_environment(client):
    assert create_version(client, 2000, environment="prod").status_code == 201
    assert create_version(client, 2000, environment="staging").status_code == 201

    for environment in ("prod", "staging"):
        report = client.post(
            "/telemetry/failures",
            headers=READER_HEADERS,
            json={
                "config_name": "checkout-service.timeout",
                "environment": environment,
                "target": "checkout-service",
                "source": "demo-client",
                "error_type": "RuntimeError",
                "fingerprint": f"{environment:0<32}"[:32],
                "anonymous_installation_id": f"anon-{environment}-installation-1234567890",
                "config_version": 1,
                "config_source": "stable",
                "sdk_version": "0.1.0",
                "app_version": "demo-client",
                "runtime": "python-3.14.3",
                "metadata": {"safe_note": environment},
            },
        )
        assert report.status_code == 202, report.text

    prod_summary = client.get(
        "/telemetry/failures/summary",
        headers=ADMIN_HEADERS,
        params={"config_name": "checkout-service.timeout", "environment": "prod", "window_minutes": 60},
    )
    staging_summary = client.get(
        "/telemetry/failures/summary",
        headers=ADMIN_HEADERS,
        params={"config_name": "checkout-service.timeout", "environment": "staging", "window_minutes": 60},
    )

    assert prod_summary.status_code == 200
    assert staging_summary.status_code == 200
    assert len(prod_summary.json()) == 1
    assert len(staging_summary.json()) == 1
    assert prod_summary.json()[0]["environment"] == "prod"
    assert staging_summary.json()[0]["environment"] == "staging"


def test_missing_config_endpoints_return_404(client):
    get_response = client.get("/configs/missing.config", headers=READER_HEADERS)
    versions_response = client.get("/configs/missing.config/versions", headers=READER_HEADERS)
    dry_run_response = client.post(
        "/configs/missing.config/schema/dry-run",
        headers=ADMIN_HEADERS,
        json={
            "environment": "prod",
            "schema": SCHEMA,
            "value": {"timeout_ms": 2000},
        },
    )

    assert get_response.status_code == 404
    assert versions_response.status_code == 404
    assert dry_run_response.status_code == 404


def test_latest_read_uses_cached_payload_when_database_session_is_unavailable(client, monkeypatch):
    assert create_version(client, 2000).status_code == 201
    container = client.app.state.container

    def broken_session():
        raise AssertionError("database session should not be used for cached latest reads")

    monkeypatch.setattr(container.database, "session", broken_session)

    response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "latest", "environment": "prod"},
    )
    assert response.status_code == 200
    assert response.json()["version"] == 1
    assert response.json()["source"] == "latest"


def test_invalid_environment_values_return_422(client):
    config_response = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"environment": "qa"},
    )
    telemetry_response = client.post(
        "/telemetry/failures",
        headers=READER_HEADERS,
        json={
            "config_name": "checkout-service.timeout",
            "environment": "qa",
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

    assert config_response.status_code == 422
    assert telemetry_response.status_code == 422


def test_websocket_invalid_environment_is_rejected(client):
    with pytest.raises(Exception):
        with client.websocket_connect(
            "/watch/ws?config_name=checkout-service.timeout&environment=qa&target=checkout-service",
            headers=READER_HEADERS,
        ):
            pass


def test_schema_validation_failure_returns_422_without_creating_a_new_version(client):
    assert create_version(client, 2000).status_code == 201

    invalid = client.post(
        "/configs",
        headers=ADMIN_HEADERS,
        json={
            "name": "checkout-service.timeout",
            "environment": "prod",
            "value": {"timeout_ms": 0},
        },
    )
    assert invalid.status_code == 422

    versions = client.get(
        "/configs/checkout-service.timeout/versions",
        headers=READER_HEADERS,
        params={"environment": "prod"},
    )
    assert versions.status_code == 200
    assert [item["version"] for item in versions.json()] == [1]


def test_create_rollout_and_rollback_write_audit_entries(client):
    assert create_version(client, 2000, "baseline").status_code == 201
    assert create_version(client, 3500, "candidate").status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 10},
    )
    assert rollout.status_code == 200, rollout.text

    rollback = client.post(
        "/configs/checkout-service.timeout/rollback",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "target_version": 1},
    )
    assert rollback.status_code == 200, rollback.text

    audit = client.get(
        "/audit",
        headers=ADMIN_HEADERS,
        params={"name": "checkout-service.timeout", "environment": "prod"},
    )
    assert audit.status_code == 200
    actions = [item["action"] for item in audit.json()]
    assert actions.count("config.create") == 2
    assert "config.rollout" in actions
    assert "config.rollback" in actions


def test_longpoll_timeout_returns_204_when_no_matching_event_arrives(client):
    response = client.get(
        "/watch/longpoll",
        headers=READER_HEADERS,
        params={
            "last_sequence": 0,
            "config_name": "checkout-service.timeout",
            "environment": "prod",
            "target": "checkout-service",
            "timeout": 0.05,
        },
    )
    assert response.status_code == 204


def test_metrics_endpoint_exposes_benchmark_metrics(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2500).status_code == 201

    with client.websocket_connect(
        "/watch/ws?config_name=checkout-service.timeout&environment=prod&target=checkout-service",
        headers=READER_HEADERS,
    ) as websocket:
        websocket.receive_json()
        rollout = client.post(
            "/configs/checkout-service.timeout/rollout",
            headers=ADMIN_HEADERS,
            json={"target": "checkout-service", "environment": "prod", "percent": 10},
        )
        assert rollout.status_code == 200, rollout.text
        event = websocket.receive_json()
        assert event["event"] == "rollout_started"

    longpoll = client.get(
        "/watch/longpoll",
        headers=READER_HEADERS,
        params={
            "last_sequence": 0,
            "config_name": "checkout-service.timeout",
            "environment": "prod",
            "target": "checkout-service",
            "timeout": 0.1,
        },
    )
    assert longpoll.status_code == 200, longpoll.text

    resolved = client.get(
        "/configs/checkout-service.timeout",
        headers=READER_HEADERS,
        params={"version": "resolved", "environment": "prod", "target": "checkout-service", "client_id": "metrics-client"},
    )
    assert resolved.status_code == 200

    rollback = client.post(
        "/configs/checkout-service.timeout/rollback",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "target_version": 1},
    )
    assert rollback.status_code == 200, rollback.text

    metrics = client.get("/metrics")
    assert metrics.status_code == 200
    body = metrics.text
    assert "config_service_config_fetch_total" in body
    assert "config_service_config_publish_total" in body
    assert "config_service_config_rollback_total" in body
    assert "config_service_config_fetch_latency_seconds_bucket" in body
    assert "config_service_config_publish_latency_seconds_bucket" in body
    assert "config_service_config_delivery_latency_seconds_bucket" in body
    assert "config_service_websocket_updates_total" in body
    assert "config_service_longpoll_updates_total" in body


def test_manual_rollback_after_immediate_promotion_returns_valid_response(client):
    assert create_version(client, 2000).status_code == 201
    assert create_version(client, 2600).status_code == 201

    rollout = client.post(
        "/configs/checkout-service.timeout/rollout",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "percent": 100},
    )
    assert rollout.status_code == 200, rollout.text
    assert rollout.json()["status"] == "promoted"

    rollback = client.post(
        "/configs/checkout-service.timeout/rollback",
        headers=ADMIN_HEADERS,
        json={"target": "checkout-service", "environment": "prod", "target_version": 1},
    )
    assert rollback.status_code == 200, rollback.text
    assert rollback.json()["status"] == "rolled_back"
    assert rollback.json()["rollout_id"].startswith("manual-checkout-service.timeout-prod-checkout-service-1")
    assert rollback.json()["created_at"]
