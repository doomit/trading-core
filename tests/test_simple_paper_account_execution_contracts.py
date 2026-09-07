import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


def _validate(schema_name: str, value: dict) -> None:
    schema = json.loads((files("trading_core.schemas") / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _paper_account() -> dict:
    return {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10000000.0,
        "realized_pnl_usd": 125.0,
        "balance_usd": 10000125.0,
        "updated_at": "2026-09-07T17:30:15Z",
        "last_execution_id": "exec-mes-173015-reduce",
    }


def test_paper_account_contract_tracks_only_durable_realized_account_state():
    value = _paper_account()
    _validate("paper_account_state_v1.schema.json", value)

    live = dict(value)
    live["mode"] = "LIVE"
    with pytest.raises(ValidationError):
        _validate("paper_account_state_v1.schema.json", live)


def test_paper_account_contract_rejects_ambiguous_closed_action_counter():
    value = _paper_account()
    value["closed_action_count"] = 3
    with pytest.raises(ValidationError):
        _validate("paper_account_state_v1.schema.json", value)


def test_paper_execution_contract_records_exact_action_price_quantity_and_realized_pnl():
    value = {
        "schema": "paper_execution_log_v1",
        "execution_id": "exec-mes-173015-reduce",
        "cycle_id": "cycle-20260907-173015",
        "symbol": "MES",
        "plan_id": "plan-mes-1715",
        "position_id": "MES-20260907-0907-01",
        "position_version_before": 4,
        "position_version_after": 5,
        "action": "REDUCE",
        "side": "SELL",
        "qty": 1,
        "price": 6510.25,
        "market_bar_end": "2026-09-07T17:30:00Z",
        "executed_at": "2026-09-07T17:30:15Z",
        "synthetic": False,
        "reason": "PLAN_REDUCE_TRIGGER",
        "realized_pnl_usd": 50.0,
    }
    _validate("paper_execution_log_v1.schema.json", value)


def test_paper_execution_contract_rejects_unknown_action_or_zero_quantity():
    value = {
        "schema": "paper_execution_log_v1",
        "execution_id": "exec-bad",
        "cycle_id": "cycle-20260907-173015",
        "symbol": "MES",
        "plan_id": "plan-mes-1715",
        "position_id": "MES-20260907-0907-01",
        "position_version_before": 4,
        "position_version_after": 5,
        "action": "PYRAMID",
        "side": "BUY",
        "qty": 0,
        "price": 6510.25,
        "market_bar_end": "2026-09-07T17:30:00Z",
        "executed_at": "2026-09-07T17:30:15Z",
        "synthetic": False,
        "reason": "BAD",
        "realized_pnl_usd": 0.0,
    }
    with pytest.raises(ValidationError):
        _validate("paper_execution_log_v1.schema.json", value)
