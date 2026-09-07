import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def _validate(value: dict) -> None:
    schema = json.loads(
        (files("trading_core.schemas") / "position_state_v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def test_current_position_contract_rejects_closed_history_state():
    closed = {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "CLOSED",
        "position_id": "MES-20260907-0907-01",
        "position_version": 5,
        "side": "LONG",
        "qty": 0,
        "avg_entry": 6500.25,
        "stop_loss": 6495.0,
        "take_profit": 6512.0,
        "active_plan_id": "plan-mes-0945",
        "active_plan_generated_at": "2026-09-07T16:45:20Z",
        "action_valid_until": "2026-09-07T17:01:00Z",
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": "2026-09-07T16:07:12Z",
        "updated_at": "2026-09-07T17:20:00Z",
        "closed_at": "2026-09-07T17:20:00Z",
        "last_action_id": "exec-mes-close",
    }
    with pytest.raises(ValidationError):
        _validate(closed)
