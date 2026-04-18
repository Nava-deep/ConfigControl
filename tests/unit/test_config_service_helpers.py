from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from app.core.settings import Settings
from app.services.config_service import ConfigService


def build_service(default_target: str = "default") -> ConfigService:
    return ConfigService(
        settings=Settings(default_target=default_target, use_redis=False),
        database=Mock(),
        cache=Mock(),
        notifications=Mock(),
    )


def test_infer_target_uses_prefix_before_dot():
    service = build_service()

    assert service.infer_target("payment-service.flags") == "payment-service"


def test_infer_target_uses_default_when_name_has_no_dot():
    service = build_service(default_target="platform-default")

    assert service.infer_target("featureflags") == "platform-default"


def test_latest_cache_key_includes_environment():
    assert ConfigService._latest_cache_key("payment-service.flags", "prod") == "config:prod:payment-service.flags:latest"


def test_version_cache_key_includes_version():
    assert (
        ConfigService._version_cache_key("payment-service.flags", "prod", 3)
        == "config:prod:payment-service.flags:version:3"
    )


def test_coerce_datetime_accepts_datetime_instance():
    value = datetime.now(timezone.utc)

    assert ConfigService._coerce_datetime(value) is value


def test_coerce_datetime_parses_iso_string():
    value = ConfigService._coerce_datetime("2026-04-18T10:00:00+00:00")

    assert value.year == 2026
    assert value.tzinfo is not None


def test_payload_to_read_response_builds_typed_response():
    service = build_service()

    response = service._payload_to_read_response(  # noqa: SLF001
        {
            "name": "payment-service.flags",
            "environment": "prod",
            "version": 2,
            "value": {"enable_sca": True},
            "schema": {"type": "object"},
            "labels": {"team": "payments"},
            "description": "flags",
            "created_at": "2026-04-18T10:00:00+00:00",
        },
        target="payment-service",
        source="stable",
    )

    assert response.name == "payment-service.flags"
    assert response.target == "payment-service"
    assert response.source == "stable"
    assert response.value["enable_sca"] is True


def test_diff_values_reports_added_removed_and_changed_fields():
    service = build_service()

    changes = service._diff_values(  # noqa: SLF001
        {"timeout_ms": 2000, "obsolete": True, "nested": {"a": 1}},
        {"timeout_ms": 2500, "nested": {"a": 2}, "retry_budget": 3},
    )

    by_path = {change.path: change.change_type for change in changes}
    assert by_path["timeout_ms"] == "changed"
    assert by_path["obsolete"] == "removed"
    assert by_path["retry_budget"] == "added"
    assert by_path["nested.a"] == "changed"


def test_diff_values_treats_changed_lists_as_single_change():
    service = build_service()

    changes = service._diff_values([1, 2], [1, 2, 3])  # noqa: SLF001

    assert len(changes) == 1
    assert changes[0].path == "$"
    assert changes[0].change_type == "changed"


def test_is_canary_client_is_deterministic_for_same_inputs():
    service = build_service()

    first = service._is_canary_client("payment-service.flags", "prod", "payment-service", "client-123", 10)  # noqa: SLF001
    second = service._is_canary_client("payment-service.flags", "prod", "payment-service", "client-123", 10)  # noqa: SLF001

    assert first is second


def test_is_canary_client_always_true_for_full_percentage():
    service = build_service()

    assert service._is_canary_client("payment-service.flags", "prod", "payment-service", "client-123", 100) is True  # noqa: SLF001
