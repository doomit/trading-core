from __future__ import annotations

import inspect
import json
from datetime import datetime, timezone
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker

from trading_core import simple_paper_contracts as contracts
from trading_core import simple_paper_position_execution as execution


NOW = datetime(2026, 9, 9, 19, 30, tzinfo=timezone.utc)


def _schema(name: str) -> dict:
    path = files("trading_core.schemas") / name
    assert path.is_file(), f"missing required schema: {name}"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: dict) -> None:
    Draft202012Validator(_schema(name), format_checker=FormatChecker()).validate(value)


def test_default_account_identity_is_explicit_and_safe():
    assert getattr(contracts, "DEFAULT_PAPER_ACCOUNT_ID", None) == "simple-paper-v1"
    validator = getattr(contracts, "validate_paper_account_id", None)
    assert callable(validator), "validate_paper_account_id must be a public contract helper"
    assert validator("paper-aggressive-a2") == "paper-aggressive-a2"


def test_new_flat_position_accepts_account_id_and_emits_v2_state():
    assert "account_id" in inspect.signature(execution.new_flat_position).parameters
    position = execution.new_flat_position("MES", NOW, account_id="paper-a")
    assert position["schema"] == "position_state_v2"
    assert position["account_id"] == "paper-a"
    assert position["event_sequence"] == 0
    assert position["last_event_id"] is None
    _validate("position_state_v2.schema.json", position)


def test_new_paper_account_emits_strict_paper_only_v2_identity():
    factory = getattr(contracts, "new_paper_account", None)
    assert callable(factory), "new_paper_account must be defined by the canonical core"
    account = factory("paper-b", NOW)
    assert account["schema"] == "paper_account_state_v2"
    assert account["account_id"] == "paper-b"
    assert account["account_type"] == "PAPER"
    assert account["broker"] == "INTERNAL_PAPER"
    assert account["environment"] == "paper"
    _validate("paper_account_state_v2.schema.json", account)


def test_v2_execution_contract_requires_account_execution_domain():
    execution_record = {
        "schema": "paper_execution_log_v2",
        "execution_id": "exec-paper-a-open",
        "cycle_id": "cycle-20260909-193000",
        "account_id": "paper-a",
        "account_type": "PAPER",
        "broker": "INTERNAL_PAPER",
        "environment": "paper",
        "symbol": "MES",
        "plan_id": "plan-mes-1930",
        "position_id": "MES-paper-a-01",
        "position_version_before": None,
        "position_version_after": 0,
        "action": "OPEN",
        "side": "BUY",
        "qty": 2,
        "price": 6500.25,
        "market_bar_end": "2026-09-09T19:30:00Z",
        "executed_at": "2026-09-09T19:30:15Z",
        "synthetic": False,
        "reason": "PLAN_OPEN_TRIGGER",
        "realized_pnl_usd": 0.0,
    }
    _validate("paper_execution_log_v2.schema.json", execution_record)


def test_v1_contracts_remain_packaged_for_legacy_read_compatibility():
    for name in (
        "position_state_v1.schema.json",
        "paper_account_state_v1.schema.json",
        "paper_execution_log_v1.schema.json",
    ):
        assert _schema(name)["$id"]
