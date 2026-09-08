from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone

from trading_core.simple_paper_dashboard import (
    DashboardThresholds,
    build_simple_paper_dashboard,
    render_simple_paper_dashboard_html,
)
from trading_core.simple_paper_position_execution import new_flat_position


NOW = datetime(2026, 9, 8, 3, 30, 30, tzinfo=timezone.utc)
THRESHOLDS = DashboardThresholds(
    feed_stale_ms=90_000,
    github_mirror_stale_ms=90_000,
    cycle_late_ms=30_000,
    brain_late_ms=20 * 60_000,
)


def _market(symbol: str, *, latest="2026-09-08T03:30:00Z", mirrored="2026-09-08T03:30:01Z"):
    return {
        "schema": "market_snapshot_v1",
        "symbol": symbol,
        "updated_at": mirrored,
        "latest_bar_end": latest,
        "db_committed_at": mirrored,
        "github_mirrored_at": mirrored,
        "bars": [{"start": "2026-09-08T03:29:00Z", "end": latest, "open": 6500.0, "high": 6502.0, "low": 6499.0, "close": 6501.0, "volume": 100.0}],
    }


def _ingest(symbol: str, *, github_status="SUCCESS", error=None):
    return {
        "schema": "ingest_log_v1",
        "log_id": f"ingest-{symbol}",
        "symbol": symbol,
        "request_id": f"req-{symbol}",
        "logged_at": "2026-09-08T03:30:02Z",
        "latest_bar_end": "2026-09-08T03:30:00Z",
        "received_at": "2026-09-08T03:30:01Z",
        "db_committed_at": "2026-09-08T03:30:01.400Z",
        "github_status": github_status,
        "github_mirrored_at": "2026-09-08T03:30:01.800Z" if github_status == "SUCCESS" else None,
        "bars_received": 20,
        "bars_inserted": 1,
        "bars_updated": 19,
        "market_to_webhook_latency_ms": 1000,
        "db_write_latency_ms": 400,
        "github_mirror_latency_ms": 400 if github_status == "SUCCESS" else None,
        "error": error,
    }


def _observation(symbol: str, *, outcome="ACCEPTED", error=None):
    return {
        "schema": "plan_observation_log_v1",
        "log_id": f"obs-{symbol}",
        "symbol": symbol,
        "pulled_at": "2026-09-08T03:30:04Z",
        "candidate_plan_id": f"plan-{symbol}",
        "candidate_generated_at": "2026-09-08T03:30:02Z",
        "analysis_bar_end": "2026-09-08T03:30:00Z",
        "current_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "outcome": outcome,
        "brain_generation_latency_ms": 2000,
        "plan_pickup_latency_ms": 2000,
        "error": error,
    }


def _cycle(symbol: str, *, tick="2026-09-08T03:30:15Z", outcome="NO_ACTION", error=None):
    return {
        "schema": "execution_cycle_log_v1",
        "log_id": f"cycle-{symbol}-{tick}",
        "cycle_id": "cycle-033015",
        "tick_at": tick,
        "symbol": symbol,
        "latest_market_end": "2026-09-08T03:30:00Z",
        "market_age_ms": 15_000,
        "start_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "end_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "plan_id": f"plan-{symbol}",
        "outcome": outcome,
        "synthetic": False,
        "eod_forced": False,
        "stale_feed": False,
        "error": error,
    }


def _plan(symbol: str):
    return {
        "schema": "trading_plan_v2",
        "plan_id": f"plan-{symbol}",
        "symbol": symbol,
        "analysis_bar_end": "2026-09-08T03:30:00Z",
        "generated_at": "2026-09-08T03:30:02Z",
        "action_valid_until": "2026-09-08T03:44:59Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "NO_TRADE",
        "side": None,
        "confidence": 0.5,
        "analysis_summary": ["No action."],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }


def _account():
    return {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10_000_000.0,
        "realized_pnl_usd": 125.0,
        "balance_usd": 10_000_125.0,
        "updated_at": "2026-09-08T03:30:10Z",
        "last_execution_id": "exec-mes-1",
    }


def _brain_run(*, blocker=None, generated="2026-09-08T03:30:02Z"):
    return {
        "schema": "simple_paper_brain_run_v1",
        "run_id": "brain-0330",
        "scheduled_run_time": "2026-09-08T03:30:00Z",
        "generated_at": generated,
        "mode": "PAPER",
        "symbols": {
            symbol: {
                "market_snapshot": {"updated_at": "2026-09-08T03:30:01Z", "latest_bar_end": "2026-09-08T03:30:00Z"},
                "position": {"updated_at": "2026-09-08T03:30:00Z", "status": "FLAT", "position_id": None, "position_version": None},
                "previous_plan": None,
                "analysis_bar_end": "2026-09-08T03:30:00Z",
                "output_validation": "passed" if blocker is None else "not_attempted",
                "published_plan": {"plan_id": f"plan-{symbol}", "generated_at": generated, "analysis_bar_end": "2026-09-08T03:30:00Z"} if blocker is None else None,
                "blocker": blocker,
            }
            for symbol in ("MES", "MNQ")
        },
        "result": "published" if blocker is None else "blocked_or_failed",
        "evidence_published": True,
        "evidence_error": None,
    }


def _inputs():
    return {
        "market_snapshots": {symbol: _market(symbol) for symbol in ("MES", "MNQ")},
        "positions": {symbol: new_flat_position(symbol, NOW) for symbol in ("MES", "MNQ")},
        "plans": {symbol: _plan(symbol) for symbol in ("MES", "MNQ")},
        "ingest_logs": [_ingest("MES"), _ingest("MNQ")],
        "plan_observation_logs": [_observation("MES"), _observation("MNQ")],
        "execution_logs": [],
        "cycle_logs": [_cycle("MES"), _cycle("MNQ")],
        "account": _account(),
        "brain_run": _brain_run(),
    }


def _build(data):
    return build_simple_paper_dashboard(now=NOW, thresholds=THRESHOLDS, **data)


def test_healthy_projection_exposes_explicit_latency_and_state():
    result = _build(_inputs())

    assert result["overall_health"] == "GREEN"
    assert result["reasons"] == []
    assert result["account"]["balance_usd"] == 10_000_125.0
    mes = result["symbols"]["MES"]
    assert mes["feed_age_ms"] == 30_000
    assert mes["github_market_mirror_age_ms"] == 29_000
    assert mes["market_to_webhook_latency_ms"] == 1000
    assert mes["db_write_latency_ms"] == 400
    assert mes["github_mirror_latency_ms"] == 400
    assert mes["brain_generation_latency_ms"] == 2000
    assert mes["plan_pickup_latency_ms"] == 2000
    assert mes["cycle_heartbeat_age_ms"] == 15_000
    assert mes["position"]["status"] == "FLAT"
    assert mes["current_plan"]["plan_id"] == "plan-MES"


def test_degraded_projection_lists_github_and_brain_reasons_without_calling_runtime_unhealthy():
    data = _inputs()
    data["ingest_logs"][0] = _ingest("MES", github_status="FAILED", error="github mirror write failed")
    data["brain_run"] = _brain_run(blocker="previous plan invalid")

    result = _build(data)

    assert result["overall_health"] == "DEGRADED"
    assert "MES:GITHUB_MIRROR_FAILED" in result["reasons"]
    assert "MES:BRAIN_BLOCKED" in result["reasons"]
    assert result["symbols"]["MES"]["feed_age_ms"] == 30_000


def test_stale_feed_or_late_cycle_is_red_from_explicit_timestamps():
    data = _inputs()
    data["market_snapshots"]["MES"] = _market("MES", latest="2026-09-08T03:28:00Z", mirrored="2026-09-08T03:28:01Z")
    data["cycle_logs"][0] = _cycle("MES", tick="2026-09-08T03:29:30Z")

    result = _build(data)

    assert result["overall_health"] == "RED"
    assert "MES:FEED_STALE" in result["reasons"]
    assert "MES:CYCLE_LATE" in result["reasons"]


def test_active_position_latest_execution_and_account_are_projected():
    data = _inputs()
    data["positions"]["MES"] = {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-20260907-2000-01",
        "position_version": 2,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 6500.0,
        "stop_loss": 6490.0,
        "take_profit": 6520.0,
        "active_plan_id": "plan-MES",
        "active_plan_generated_at": "2026-09-08T03:30:02Z",
        "action_valid_until": "2026-09-08T03:44:59Z",
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": "2026-09-08T03:30:05Z",
        "updated_at": "2026-09-08T03:30:10Z",
        "closed_at": None,
        "last_action_id": "plan-MES:open",
    }
    data["execution_logs"] = [{
        "schema": "paper_execution_log_v1",
        "execution_id": "exec-mes-1",
        "cycle_id": "cycle-033015",
        "symbol": "MES",
        "plan_id": "plan-MES",
        "position_id": "MES-20260907-2000-01",
        "position_version_before": None,
        "position_version_after": 0,
        "action": "OPEN",
        "side": "BUY",
        "qty": 2,
        "price": 6500.0,
        "market_bar_end": "2026-09-08T03:30:00Z",
        "executed_at": "2026-09-08T03:30:05Z",
        "synthetic": False,
        "reason": "PLAN_OPEN",
        "realized_pnl_usd": 0.0,
    }]

    result = _build(data)

    mes = result["symbols"]["MES"]
    assert mes["position"]["position_version"] == 2
    assert mes["position"]["active_plan_id"] == "plan-MES"
    assert mes["latest_execution"]["action"] == "OPEN"
    assert mes["latest_execution"]["price"] == 6500.0


def test_missing_authoritative_position_or_cycle_error_is_red_with_explicit_reason():
    data = _inputs()
    data["positions"]["MNQ"] = None
    data["cycle_logs"][1] = _cycle("MNQ", outcome="ERROR", error="position store unavailable")

    result = _build(data)

    assert result["overall_health"] == "RED"
    assert "MNQ:POSITION_MISSING" in result["reasons"]
    assert "MNQ:CYCLE_ERROR" in result["reasons"]


def test_projection_is_read_only_and_html_renders_health_metrics_and_reasons():
    data = _inputs()
    before = deepcopy(data)
    result = _build(data)
    html = render_simple_paper_dashboard_html(result)

    assert data == before
    assert "Simple Paper" in html
    assert "GREEN" in html
    assert "MES" in html and "MNQ" in html
    assert "10,000,125.00" in html
    assert "30.0s" in html
