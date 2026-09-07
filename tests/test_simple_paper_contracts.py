import json
from importlib.resources import files

import pytest
from jsonschema import Draft202012Validator, FormatChecker, ValidationError


SCHEMA_PACKAGE = "trading_core.schemas"


def _schema(name: str) -> dict:
    return json.loads((files(SCHEMA_PACKAGE) / name).read_text(encoding="utf-8"))


def _validate(name: str, value: dict) -> None:
    Draft202012Validator(_schema(name), format_checker=FormatChecker()).validate(value)


def _bar(minute: int = 45) -> dict:
    return {
        "start": f"2026-09-07T16:{minute:02d}:00Z",
        "end": f"2026-09-07T16:{minute + 1:02d}:00Z",
        "open": 6500.0,
        "high": 6502.0,
        "low": 6498.0,
        "close": 6501.0,
        "volume": 1234.0,
    }


def _flat_ref() -> dict:
    return {"state": "FLAT", "position_id": None, "position_version": None}


def _open_ref(version: int = 3) -> dict:
    return {
        "state": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": version,
    }


def _open_position() -> dict:
    return {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": 3,
        "side": "LONG",
        "qty": 4,
        "avg_entry": 6500.25,
        "stop_loss": 6495.0,
        "take_profit": 6512.0,
        "active_plan_id": "plan-mes-0945",
        "active_plan_generated_at": "2026-09-07T16:45:20Z",
        "action_valid_until": "2026-09-07T17:01:00Z",
        "pending_add": {"order_type": "STOP", "trigger_price": 6504.0, "qty": 1},
        "pending_reduce": {"order_type": "LIMIT", "trigger_price": 6509.0, "qty": 1},
        "opened_at": "2026-09-07T16:07:12Z",
        "updated_at": "2026-09-07T16:45:30Z",
        "closed_at": None,
        "last_action_id": "plan-mes-0945:apply",
    }


def _open_plan() -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-1000",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-07T17:00:00Z",
        "generated_at": "2026-09-07T17:00:20Z",
        "action_valid_until": "2026-09-07T17:16:00Z",
        "target_position": _flat_ref(),
        "decision": "OPEN",
        "side": "LONG",
        "confidence": 0.82,
        "analysis_summary": ["breakout held", "pullback remained above support"],
        "entry": {"order_type": "LIMIT", "trigger_price": 6501.0, "qty": 4},
        "protection": {"stop_loss": 6495.0, "take_profit": 6512.0},
        "add_once": {"order_type": "STOP", "trigger_price": 6504.0, "qty": 1},
        "reduce_once": {"order_type": "LIMIT", "trigger_price": 6509.0, "qty": 1},
    }


def test_market_snapshot_contract_accepts_bounded_one_minute_window():
    value = {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "updated_at": "2026-09-07T16:46:02Z",
        "latest_bar_end": "2026-09-07T16:46:00Z",
        "db_committed_at": "2026-09-07T16:46:02Z",
        "github_mirrored_at": "2026-09-07T16:46:03Z",
        "bars": [_bar(44), _bar(45)],
    }
    _validate("market_snapshot_v1.schema.json", value)


def test_market_snapshot_rejects_unknown_symbol_and_extra_executable_shape():
    value = {
        "schema": "market_snapshot_v1",
        "symbol": "ES",
        "updated_at": "2026-09-07T16:46:02Z",
        "latest_bar_end": "2026-09-07T16:46:00Z",
        "db_committed_at": "2026-09-07T16:46:02Z",
        "github_mirrored_at": "2026-09-07T16:46:03Z",
        "bars": [_bar(45)],
        "signal": "BUY",
    }
    with pytest.raises(ValidationError):
        _validate("market_snapshot_v1.schema.json", value)


def test_position_contract_accepts_open_position_with_durable_protection():
    _validate("position_state_v1.schema.json", _open_position())


def test_position_contract_rejects_quantity_above_six_and_open_without_protection():
    too_large = _open_position()
    too_large["qty"] = 7
    with pytest.raises(ValidationError):
        _validate("position_state_v1.schema.json", too_large)

    unprotected = _open_position()
    unprotected["stop_loss"] = None
    with pytest.raises(ValidationError):
        _validate("position_state_v1.schema.json", unprotected)


def test_position_contract_accepts_flat_snapshot_without_position_identity():
    value = {
        "schema": "position_state_v1",
        "symbol": "MNQ",
        "status": "FLAT",
        "position_id": None,
        "position_version": None,
        "side": None,
        "qty": 0,
        "avg_entry": None,
        "stop_loss": None,
        "take_profit": None,
        "active_plan_id": None,
        "active_plan_generated_at": None,
        "action_valid_until": None,
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": None,
        "updated_at": "2026-09-07T17:00:00Z",
        "closed_at": None,
        "last_action_id": None,
    }
    _validate("position_state_v1.schema.json", value)


def test_execution_rules_contract_is_paper_only_and_caps_quantity_at_six():
    value = {
        "schema": "execution_rules_v1",
        "updated_at": "2026-09-07T15:00:00Z",
        "mode": "PAPER",
        "symbols": ["MES", "MNQ"],
        "max_contracts_per_symbol": 6,
        "one_active_position_per_symbol": True,
        "paper_initial_cash_usd": 10000000,
        "eod": {"timezone": "America/Los_Angeles", "force_close_time": "15:00:00"},
        "stale_feed_forced_stop_minutes": 15,
        "same_bar_exit_policy": "ADVERSE_FIRST",
    }
    _validate("execution_rules_v1.schema.json", value)

    live = dict(value)
    live["mode"] = "LIVE"
    with pytest.raises(ValidationError):
        _validate("execution_rules_v1.schema.json", live)

    too_many = dict(value)
    too_many["max_contracts_per_symbol"] = 7
    with pytest.raises(ValidationError):
        _validate("execution_rules_v1.schema.json", too_many)


def test_plan_contract_accepts_flat_open_plan_with_exact_executable_keywords():
    _validate("trading_plan_v2.schema.json", _open_plan())


def test_plan_contract_rejects_open_plan_bound_to_existing_position_or_missing_protection():
    wrong_ref = _open_plan()
    wrong_ref["target_position"] = _open_ref()
    with pytest.raises(ValidationError):
        _validate("trading_plan_v2.schema.json", wrong_ref)

    missing_stop = _open_plan()
    missing_stop["protection"] = {"take_profit": 6512.0}
    with pytest.raises(ValidationError):
        _validate("trading_plan_v2.schema.json", missing_stop)


def test_plan_contract_accepts_update_for_exact_position_version_and_rejects_unknown_action_keyword():
    value = {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-1015",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-07T17:15:00Z",
        "generated_at": "2026-09-07T17:15:18Z",
        "action_valid_until": "2026-09-07T17:31:00Z",
        "target_position": _open_ref(version=3),
        "decision": "UPDATE",
        "side": "LONG",
        "confidence": 0.71,
        "analysis_summary": ["raise protection after continuation"],
        "entry": None,
        "protection": {"stop_loss": 6499.0, "take_profit": 6515.0},
        "add_once": None,
        "reduce_once": {"order_type": "LIMIT", "trigger_price": 6510.0, "qty": 1},
    }
    _validate("trading_plan_v2.schema.json", value)

    bad = dict(value)
    bad["decision"] = "PYRAMID_AGGRESSIVELY"
    with pytest.raises(ValidationError):
        _validate("trading_plan_v2.schema.json", bad)


def test_plan_contract_rejects_malformed_position_reference():
    value = _open_plan()
    value["target_position"] = {
        "state": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": None,
    }
    with pytest.raises(ValidationError):
        _validate("trading_plan_v2.schema.json", value)


def test_ingest_log_contract_carries_latency_and_mirror_status():
    value = {
        "schema": "ingest_log_v1",
        "log_id": "ingest-mes-164602",
        "symbol": "MES",
        "request_id": "tv-req-123",
        "logged_at": "2026-09-07T16:46:03Z",
        "latest_bar_end": "2026-09-07T16:46:00Z",
        "received_at": "2026-09-07T16:46:02.100Z",
        "db_committed_at": "2026-09-07T16:46:02.180Z",
        "github_status": "SUCCESS",
        "github_mirrored_at": "2026-09-07T16:46:02.900Z",
        "bars_received": 20,
        "bars_inserted": 1,
        "bars_updated": 19,
        "market_to_webhook_latency_ms": 2100,
        "db_write_latency_ms": 80,
        "github_mirror_latency_ms": 720,
        "error": None,
    }
    _validate("ingest_log_v1.schema.json", value)


def test_plan_observation_log_contract_records_latency_and_position_match_outcome():
    value = {
        "schema": "plan_observation_log_v1",
        "log_id": "planobs-mes-170015",
        "symbol": "MES",
        "pulled_at": "2026-09-07T17:00:15Z",
        "candidate_plan_id": "plan-mes-1000",
        "candidate_generated_at": "2026-09-07T17:00:05Z",
        "analysis_bar_end": "2026-09-07T17:00:00Z",
        "current_position": _flat_ref(),
        "outcome": "ACCEPTED",
        "brain_generation_latency_ms": 5000,
        "plan_pickup_latency_ms": 10000,
        "error": None,
    }
    _validate("plan_observation_log_v1.schema.json", value)


def test_execution_cycle_log_contract_records_market_age_and_terminal_action():
    value = {
        "schema": "execution_cycle_log_v1",
        "log_id": "cycle-mes-170015",
        "cycle_id": "cycle-20260907-170015",
        "tick_at": "2026-09-07T17:00:15Z",
        "symbol": "MES",
        "latest_market_end": "2026-09-07T17:00:00Z",
        "market_age_ms": 15000,
        "start_position": _open_ref(version=3),
        "end_position": _open_ref(version=4),
        "plan_id": "plan-mes-1000",
        "outcome": "REDUCED",
        "synthetic": False,
        "eod_forced": False,
        "stale_feed": False,
        "error": None,
    }
    _validate("execution_cycle_log_v1.schema.json", value)
