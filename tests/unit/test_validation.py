from __future__ import annotations

import pytest
from jsonschema.exceptions import ValidationError

from app.services.validation import raise_for_validation, validate_payload, validate_schema


SCHEMA = {
    "type": "object",
    "properties": {
        "timeout_ms": {"type": "integer", "minimum": 1},
        "enabled": {"type": "boolean"},
    },
    "required": ["timeout_ms"],
    "additionalProperties": False,
}


def test_validate_schema_accepts_valid_schema():
    validate_schema(SCHEMA)


def test_validate_schema_rejects_invalid_schema():
    with pytest.raises(Exception):
        validate_schema({"type": "definitely-not-valid"})


@pytest.mark.parametrize(
    ("payload", "expected_fragment"),
    [
        ({"timeout_ms": 1000, "enabled": True}, None),
        ({}, "'timeout_ms' is a required property"),
        ({"timeout_ms": 1000, "unexpected": True}, "Additional properties are not allowed"),
        ({"timeout_ms": "1000"}, "'1000' is not of type 'integer'"),
    ],
)
def test_validate_payload_reports_expected_errors(payload, expected_fragment):
    errors = validate_payload(payload, SCHEMA)

    if expected_fragment is None:
        assert errors == []
    else:
        assert any(expected_fragment in error for error in errors)


def test_raise_for_validation_allows_valid_payload():
    raise_for_validation({"timeout_ms": 1000}, SCHEMA)


def test_raise_for_validation_raises_for_invalid_payload():
    with pytest.raises(ValidationError):
        raise_for_validation({"timeout_ms": 0}, SCHEMA)
