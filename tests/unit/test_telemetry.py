from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import Mock

from app.core.settings import Settings
from app.services.telemetry import TelemetryService


def build_service(*, salt: str = "test-salt") -> TelemetryService:
    return TelemetryService(settings=Settings(telemetry_hash_salt=salt), database=Mock())


def test_anonymize_installation_id_is_deterministic_for_same_salt():
    service = build_service(salt="salt-a")

    first = service._anonymize_installation_id("installation-1234567890")  # noqa: SLF001
    second = service._anonymize_installation_id("installation-1234567890")  # noqa: SLF001

    assert first == second


def test_anonymize_installation_id_changes_with_salt():
    first = build_service(salt="salt-a")._anonymize_installation_id("installation-1234567890")  # noqa: SLF001
    second = build_service(salt="salt-b")._anonymize_installation_id("installation-1234567890")  # noqa: SLF001

    assert first != second


def test_sanitize_metadata_normalizes_and_blocks_sensitive_keys():
    service = build_service()

    sanitized = service._sanitize_metadata(  # noqa: SLF001
        {
            " Safe_Note ": "kept",
            "StackTrace": "drop",
            "userName": "drop",
            "retry_count": 3,
        }
    )

    assert sanitized == {"safe_note": "kept", "retry_count": 3}


def test_sanitize_metadata_truncates_strings_and_keeps_scalars():
    service = build_service()

    sanitized = service._sanitize_metadata(  # noqa: SLF001
        {
            "message_id": "drop",
            "safe": "x" * 150,
            "enabled": True,
            "duration_ms": 42,
            "ratio": 0.5,
            "none_value": None,
        }
    )

    assert sanitized["safe"] == "x" * 120
    assert sanitized["enabled"] is True
    assert sanitized["duration_ms"] == 42
    assert sanitized["ratio"] == 0.5
    assert sanitized["none_value"] is None
    assert "message_id" not in sanitized


def test_sanitize_metadata_limits_to_first_twelve_entries():
    service = build_service()
    payload = {f"field_{index}": index for index in range(20)}

    sanitized = service._sanitize_metadata(payload)  # noqa: SLF001

    assert len(sanitized) == 12
    assert "field_11" in sanitized
    assert "field_12" not in sanitized


def test_ensure_utc_adds_timezone_for_naive_datetime():
    service = build_service()
    naive = datetime(2026, 4, 10, 12, 0, 0)

    coerced = service._ensure_utc(naive)  # noqa: SLF001

    assert coerced.tzinfo == timezone.utc


def test_ensure_utc_preserves_existing_timezone():
    service = build_service()
    aware = datetime(2026, 4, 10, 12, 0, 0, tzinfo=timezone.utc)

    coerced = service._ensure_utc(aware)  # noqa: SLF001

    assert coerced is aware
