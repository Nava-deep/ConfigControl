from __future__ import annotations

import pytest

from app.services.validation import validate_payload


@pytest.mark.parametrize(
    ("schema", "payload", "expected_error_fragment"),
    [
        (
            {
                "type": "object",
                "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
                "required": ["timeout_ms"],
                "additionalProperties": False,
            },
            {"timeout_ms": 1000},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
                "required": ["timeout_ms"],
                "additionalProperties": False,
            },
            {"timeout_ms": 0},
            "less than the minimum",
        ),
        (
            {
                "type": "object",
                "properties": {"timeout_ms": {"type": "integer", "minimum": 1}},
                "required": ["timeout_ms"],
                "additionalProperties": False,
            },
            {},
            "required property",
        ),
        (
            {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["shadow", "active"]}},
                "required": ["mode"],
                "additionalProperties": False,
            },
            {"mode": "shadow"},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"mode": {"type": "string", "enum": ["shadow", "active"]}},
                "required": ["mode"],
                "additionalProperties": False,
            },
            {"mode": "disabled"},
            "is not one of",
        ),
        (
            {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
                "additionalProperties": False,
            },
            {"enabled": True},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"enabled": {"type": "boolean"}},
                "required": ["enabled"],
                "additionalProperties": False,
            },
            {"enabled": "true"},
            "is not of type 'boolean'",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "limits": {
                        "type": "object",
                        "properties": {"rpm": {"type": "integer", "minimum": 1}},
                        "required": ["rpm"],
                        "additionalProperties": False,
                    }
                },
                "required": ["limits"],
                "additionalProperties": False,
            },
            {"limits": {"rpm": 600}},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {
                    "limits": {
                        "type": "object",
                        "properties": {"rpm": {"type": "integer", "minimum": 1}},
                        "required": ["rpm"],
                        "additionalProperties": False,
                    }
                },
                "required": ["limits"],
                "additionalProperties": False,
            },
            {"limits": {"rpm": 0}},
            "less than the minimum",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "limits": {
                        "type": "object",
                        "properties": {"rpm": {"type": "integer", "minimum": 1}},
                        "required": ["rpm"],
                        "additionalProperties": False,
                    }
                },
                "required": ["limits"],
                "additionalProperties": False,
            },
            {"limits": {"rpm": 100, "burst": 20}},
            "Additional properties are not allowed",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["models"],
                "additionalProperties": False,
            },
            {"models": ["baseline-v1", "contextual-v2"]},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["models"],
                "additionalProperties": False,
            },
            {"models": []},
            "should be non-empty",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "models": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    }
                },
                "required": ["models"],
                "additionalProperties": False,
            },
            {"models": ["baseline-v1", 2]},
            "is not of type 'string'",
        ),
        (
            {
                "type": "object",
                "properties": {"exploration_percent": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["exploration_percent"],
                "additionalProperties": False,
            },
            {"exploration_percent": 12},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"exploration_percent": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["exploration_percent"],
                "additionalProperties": False,
            },
            {"exploration_percent": -1},
            "less than the minimum",
        ),
        (
            {
                "type": "object",
                "properties": {"exploration_percent": {"type": "integer", "minimum": 0, "maximum": 100}},
                "required": ["exploration_percent"],
                "additionalProperties": False,
            },
            {"exploration_percent": 101},
            "greater than the maximum",
        ),
        (
            {
                "type": "object",
                "properties": {"region": {"type": "string", "pattern": "^(us|eu|ap)-[a-z]+$"}},
                "required": ["region"],
                "additionalProperties": False,
            },
            {"region": "us-east"},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"region": {"type": "string", "pattern": "^(us|eu|ap)-[a-z]+$"}},
                "required": ["region"],
                "additionalProperties": False,
            },
            {"region": "moon-base"},
            "does not match",
        ),
        (
            {
                "type": "object",
                "properties": {"safe_mode": {"type": "boolean"}, "safe_mode_rpm": {"type": "integer", "minimum": 1}},
                "required": ["safe_mode", "safe_mode_rpm"],
                "additionalProperties": False,
            },
            {"safe_mode": True, "safe_mode_rpm": 300},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"safe_mode": {"type": "boolean"}, "safe_mode_rpm": {"type": "integer", "minimum": 1}},
                "required": ["safe_mode", "safe_mode_rpm"],
                "additionalProperties": False,
            },
            {"safe_mode": True},
            "required property",
        ),
        (
            {
                "type": "object",
                "properties": {"strategy": {"type": "string"}, "weight": {"type": "number", "minimum": 0.0}},
                "required": ["strategy", "weight"],
                "additionalProperties": False,
            },
            {"strategy": "shadow", "weight": 0.25},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {"strategy": {"type": "string"}, "weight": {"type": "number", "minimum": 0.0}},
                "required": ["strategy", "weight"],
                "additionalProperties": False,
            },
            {"strategy": "shadow", "weight": -0.1},
            "less than the minimum",
        ),
        (
            {
                "type": "object",
                "properties": {"strategy": {"type": "string"}, "weight": {"type": "number", "minimum": 0.0}},
                "required": ["strategy", "weight"],
                "additionalProperties": False,
            },
            {"strategy": "shadow", "weight": 0.25, "extra": True},
            "Additional properties are not allowed",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "thresholds": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                        "maxItems": 4,
                    }
                },
                "required": ["thresholds"],
                "additionalProperties": False,
            },
            {"thresholds": [100, 200]},
            None,
        ),
        (
            {
                "type": "object",
                "properties": {
                    "thresholds": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                        "maxItems": 4,
                    }
                },
                "required": ["thresholds"],
                "additionalProperties": False,
            },
            {"thresholds": [100]},
            "is too short",
        ),
        (
            {
                "type": "object",
                "properties": {
                    "thresholds": {
                        "type": "array",
                        "items": {"type": "integer", "minimum": 1},
                        "minItems": 2,
                        "maxItems": 4,
                    }
                },
                "required": ["thresholds"],
                "additionalProperties": False,
            },
            {"thresholds": [100, 200, 300, 400, 500]},
            "is too long",
        ),
    ],
)
def test_validate_payload_matrix(schema, payload, expected_error_fragment):
    errors = validate_payload(payload, schema)

    if expected_error_fragment is None:
        assert errors == []
    else:
        assert any(expected_error_fragment in error for error in errors)
