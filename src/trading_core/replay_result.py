from __future__ import annotations

import json
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


_SCHEMA_RESOURCE = "replay_result_v1.schema.json"


def load_replay_result_schema() -> dict[str, Any]:
    return json.loads(files("trading_core.schemas").joinpath(_SCHEMA_RESOURCE).read_text(encoding="utf-8"))


def replay_result_validator() -> Draft202012Validator:
    schema = load_replay_result_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_replay_result(result: dict[str, Any]) -> None:
    replay_result_validator().validate(result)


__all__ = ["load_replay_result_schema", "replay_result_validator", "validate_replay_result"]
