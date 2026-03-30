from __future__ import annotations

from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import ValidationError


def validate_schema(schema: dict[str, Any]) -> None:
    Draft202012Validator.check_schema(schema)


def validate_payload(value: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    validate_schema(schema)
    validator = Draft202012Validator(schema)
    return [error.message for error in validator.iter_errors(value)]


def raise_for_validation(value: dict[str, Any], schema: dict[str, Any]) -> None:
    errors = validate_payload(value, schema)
    if errors:
        raise ValidationError("; ".join(errors))
