import json
from importlib.resources import files

import pytest
from jsonschema import ValidationError, validate


def schema():
    return json.loads((files("trading_core.schemas") / "brain_trigger_v1.schema.json").read_text(encoding="utf-8"))


def trigger():
    return {
        "schema": "brain_trigger_v1",
        "event_id": "evt_brain_MES1_capacity_status",
        "timestamp": "2026-09-02T14:05:30Z",
        "symbol": "MES1!",
        "trigger_source": "azure_anomaly_dispatch",
        "brain_tier": "L2",
        "anomaly": {"rule_id": "reclaim_vwap", "severity": "MEDIUM", "reason": "VWAP reclaim"},
        "baseline": {"thesis_id": "thesis-1", "plan_id": "plan-1"},
        "market_context": {
            "repository": "doomit/trading-runtime",
            "branch": "gpt-runtime",
            "path": "runtime/market-context/current.json",
            "version": "ctx-1",
        },
        "scheduler": {"minutes_to_next_deep": 9.5, "state": "COMPLETE"},
        "budget": {"events_this_hour": 2, "soft_cap": 8, "hard_cap": 10},
        "paper_only": True,
    }


def capacity():
    return {
        "mode": "PAPER",
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "open_contracts_total": 0,
        "max_open_micro_contracts": 20,
        "remaining_open_micro_contracts": 20,
        "entries_this_session": 0,
        "max_entries_per_session": 30,
        "remaining_entries_this_session": 30,
        "daily_realized_pnl_usd": 0,
        "max_daily_realized_loss_usd": 5000,
        "remaining_daily_loss_usd": 5000,
        "consecutive_failures": 0,
        "max_consecutive_losses": 4,
        "remaining_loss_streak": 4,
        "target_risk_per_trade_usd": 500,
        "max_risk_per_trade_usd": 1000,
    }


def test_brain_trigger_can_expose_capacity_enrichment_unavailable_state():
    value = trigger()
    value["account_capacity_status"] = {
        "state": "UNAVAILABLE",
        "reason": "capacity_enrichment_failed",
    }
    validate(value, schema())


def test_available_capacity_status_requires_account_capacity_payload():
    value = trigger()
    value["account_capacity_status"] = {"state": "AVAILABLE", "reason": None}
    with pytest.raises(ValidationError):
        validate(value, schema())


def test_unknown_capacity_status_is_rejected():
    value = trigger()
    value["account_capacity_status"] = {"state": "MAYBE", "reason": None}
    with pytest.raises(ValidationError):
        validate(value, schema())


def test_unavailable_capacity_status_requires_failure_reason():
    value = trigger()
    value["account_capacity_status"] = {"state": "UNAVAILABLE", "reason": None}
    with pytest.raises(ValidationError):
        validate(value, schema())


def test_available_capacity_status_cannot_claim_enrichment_failure():
    value = trigger()
    value["account_capacity"] = capacity()
    value["account_capacity_status"] = {
        "state": "AVAILABLE",
        "reason": "capacity_enrichment_failed",
    }
    with pytest.raises(ValidationError):
        validate(value, schema())
