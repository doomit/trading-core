import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def _validate(value: dict) -> None:
    schema = json.loads(
        (files("trading_core.schemas") / "trading_plan_v2.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _plan(decision: str) -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": f"plan-mes-{decision.lower()}",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-07T17:15:00Z",
        "generated_at": "2026-09-07T17:15:20Z",
        "action_valid_until": "2026-09-07T17:31:00Z",
        "target_position": {
            "state": "OPEN",
            "position_id": "MES-20260907-0907-01",
            "position_version": 3,
        },
        "decision": decision,
        "side": "LONG",
        "confidence": 0.75,
        "analysis_summary": ["decision contract fixture"],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }


def test_hold_is_non_mutating_and_cannot_override_protection_or_pending_actions():
    _validate(_plan("HOLD"))

    bad = _plan("HOLD")
    bad["protection"] = {"stop_loss": 6498.0, "take_profit": 6516.0}
    with pytest.raises(ValidationError):
        _validate(bad)

    bad = _plan("HOLD")
    bad["add_once"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    with pytest.raises(ValidationError):
        _validate(bad)


def test_exit_is_immediate_and_cannot_mix_with_update_instructions():
    _validate(_plan("EXIT"))

    bad = _plan("EXIT")
    bad["reduce_once"] = {"order_type": "LIMIT", "trigger_price": 6510.0, "qty": 1}
    with pytest.raises(ValidationError):
        _validate(bad)

    bad = _plan("EXIT")
    bad["protection"] = {"stop_loss": 6498.0, "take_profit": 6516.0}
    with pytest.raises(ValidationError):
        _validate(bad)


def test_update_must_change_at_least_one_protection_or_pending_action():
    valid = _plan("UPDATE")
    valid["protection"] = {"stop_loss": 6498.0, "take_profit": 6516.0}
    _validate(valid)

    with pytest.raises(ValidationError):
        _validate(_plan("UPDATE"))


def test_update_can_change_only_one_pending_action_without_rewriting_protection():
    valid = _plan("UPDATE")
    valid["add_once"] = {"order_type": "STOP", "trigger_price": 6506.0, "qty": 1}
    _validate(valid)
