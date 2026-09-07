import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def _validate(value: dict) -> None:
    schema = json.loads(
        (files("trading_core.schemas") / "trading_plan_v2.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _update_plan(add_once=None, reduce_once=None) -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-update-order",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-07T17:15:00Z",
        "generated_at": "2026-09-07T17:15:20Z",
        "action_valid_until": "2026-09-07T17:31:00Z",
        "target_position": {
            "state": "OPEN",
            "position_id": "MES-20260907-0907-01",
            "position_version": 3,
        },
        "decision": "UPDATE",
        "side": "LONG",
        "confidence": 0.75,
        "analysis_summary": ["update one-shot position management"],
        "entry": None,
        "protection": {"stop_loss": 6498.0, "take_profit": 6516.0},
        "add_once": add_once,
        "reduce_once": reduce_once,
    }


def test_add_and_reduce_use_explicit_market_limit_stop_order_semantics():
    _validate(
        _update_plan(
            add_once={"order_type": "LIMIT", "trigger_price": 6499.0, "qty": 1},
            reduce_once={"order_type": "LIMIT", "trigger_price": 6510.0, "qty": 1},
        )
    )
    _validate(
        _update_plan(
            add_once={"order_type": "STOP", "trigger_price": 6506.0, "qty": 1},
            reduce_once={"order_type": "MARKET", "trigger_price": None, "qty": 1},
        )
    )


def test_market_adjustment_requires_null_trigger_and_limit_stop_require_price():
    with pytest.raises(ValidationError):
        _validate(_update_plan(add_once={"order_type": "MARKET", "trigger_price": 6500.0, "qty": 1}))

    with pytest.raises(ValidationError):
        _validate(_update_plan(reduce_once={"order_type": "LIMIT", "trigger_price": None, "qty": 1}))


def test_adjustment_without_order_type_is_rejected_as_ambiguous():
    with pytest.raises(ValidationError):
        _validate(_update_plan(add_once={"trigger_price": 6499.0, "qty": 1}))
