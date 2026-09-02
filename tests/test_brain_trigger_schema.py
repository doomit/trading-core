import json
from importlib.resources import files

import pytest
from jsonschema import ValidationError, validate


def _schema() -> dict:
    return json.loads((files("trading_core.schemas") / "brain_trigger_v1.schema.json").read_text(encoding="utf-8"))


def _trigger() -> dict:
    return {
        "schema": "brain_trigger_v1",
        "event_id": "evt_brain_MES1_capacity",
        "timestamp": "2026-09-01T20:00:00Z",
        "symbol": "MES1!",
        "trigger_source": "azure_anomaly_dispatch",
        "brain_tier": "L2",
        "anomaly": {"rule_id": "break_low", "severity": "HIGH", "reason": "session low break"},
        "baseline": {"thesis_id": "thesis-1", "plan_id": "plan-1"},
        "market_context": {
            "repository": "doomit/trading-runtime",
            "branch": "gpt-runtime",
            "path": "runtime/market-context/current.json",
            "version": "ctx-1",
        },
        "scheduler": {"minutes_to_next_deep": 3.0, "state": "IDLE"},
        "budget": {"events_this_hour": 1, "soft_cap": 8, "hard_cap": 10},
        "account_capacity": {
            "mode": "PAPER",
            "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
            "open_contracts_total": 0,
            "max_open_micro_contracts": 20,
            "remaining_open_micro_contracts": 20,
            "entries_this_session": 2,
            "max_entries_per_session": 30,
            "remaining_entries_this_session": 28,
            "daily_realized_pnl_usd": -578.0,
            "max_daily_realized_loss_usd": 5000.0,
            "remaining_daily_loss_usd": 4422.0,
            "consecutive_failures": 2,
            "max_consecutive_losses": 4,
            "remaining_loss_streak": 2,
            "target_risk_per_trade_usd": 500.0,
            "max_risk_per_trade_usd": 1000.0,
        },
        "paper_only": True,
    }


def test_brain_trigger_accepts_optional_exact_paper_account_capacity():
    validate(_trigger(), _schema())
    legacy = _trigger()
    del legacy["account_capacity"]
    validate(legacy, _schema())


def test_brain_trigger_rejects_negative_remaining_capacity():
    value = _trigger()
    value["account_capacity"]["remaining_open_micro_contracts"] = -1
    with pytest.raises(ValidationError):
        validate(value, _schema())


def test_brain_trigger_accepts_explicit_capacity_enrichment_failure_status():
    value = _trigger()
    del value["account_capacity"]
    value["account_capacity_status"] = {
        "status": "UNAVAILABLE",
        "reason_code": "ENRICHMENT_FAILED",
    }
    validate(value, _schema())
