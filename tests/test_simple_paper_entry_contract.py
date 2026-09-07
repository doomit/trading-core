import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def _validate(value: dict) -> None:
    schema = json.loads(
        (files("trading_core.schemas") / "trading_plan_v2.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _market_plan(trigger_price):
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-market-entry",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-07T17:00:00Z",
        "generated_at": "2026-09-07T17:00:20Z",
        "action_valid_until": "2026-09-07T17:16:00Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "OPEN",
        "side": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["enter immediately at the next execution opportunity"],
        "entry": {"order_type": "MARKET", "trigger_price": trigger_price, "qty": 2},
        "protection": {"stop_loss": 6495.0, "take_profit": 6512.0},
        "add_once": None,
        "reduce_once": None,
    }


def test_market_entry_requires_null_trigger_price():
    _validate(_market_plan(None))

    with pytest.raises(ValidationError):
        _validate(_market_plan(6501.0))
