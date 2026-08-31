from __future__ import annotations

import json
from datetime import datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


_SCHEMA_RESOURCE = "runtime_activity_v1.schema.json"


def load_runtime_activity_schema() -> dict[str, Any]:
    return json.loads(files("trading_core.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8"))


def _parse_occurred_at(receipt: dict[str, Any]) -> datetime:
    value = receipt.get("occurred_at")
    if not isinstance(value, str):
        raise ValueError("occurred_at must be a timezone-aware ISO-8601 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("occurred_at must be a timezone-aware ISO-8601 timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("occurred_at must be a timezone-aware ISO-8601 timestamp")
    return parsed


def runtime_activity_validator() -> Draft202012Validator:
    schema = load_runtime_activity_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_runtime_activity(receipt: dict[str, Any]) -> None:
    runtime_activity_validator().validate(receipt)
    _parse_occurred_at(receipt)


__all__ = [
    "load_runtime_activity_schema",
    "runtime_activity_validator",
    "validate_runtime_activity",
]
