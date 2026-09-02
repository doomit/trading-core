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
