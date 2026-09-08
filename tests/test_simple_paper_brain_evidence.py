from __future__ import annotations

from datetime import datetime, timezone

from trading_core.simple_paper_brain_integration import brain_run_path, market_path, plan_path, position_path, run_scheduled_brain_job
from trading_core.simple_paper_position_execution import new_flat_position


NOW = datetime(2026, 9, 8, 3, 30, tzinfo=timezone.utc)


def _market(symbol: str) -> dict:
    return {
        "schema": "market_snapshot_v1",
        "symbol": symbol,
        "updated_at": "2026-09-08T03:30:01Z",
        "latest_bar_end": "2026-09-08T03:30:00Z",
        "db_committed_at": "2026-09-08T03:30:01Z",
        "github_mirrored_at": "2026-09-08T03:30:01Z",
        "bars": [{"start": "2026-09-08T03:29:00Z", "end": "2026-09-08T03:30:00Z", "open": 6500.0, "high": 6502.0, "low": 6499.0, "close": 6501.0, "volume": 100.0}],
    }


def _rules() -> dict:
    return {
        "schema": "execution_rules_v1",
        "updated_at": "2026-09-07T16:00:00Z",
        "mode": "PAPER",
        "symbols": ["MES", "MNQ"],
        "max_contracts_per_symbol": 6,
        "one_active_position_per_symbol": True,
        "paper_initial_cash_usd": 10000000,
        "eod": {"timezone": "America/Los_Angeles", "force_close_time": "15:00:00"},
        "stale_feed_forced_stop_minutes": 15,
        "same_bar_exit_policy": "ADVERSE_FIRST",
    }


def _plan(symbol: str) -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": f"plan-{symbol.lower()}-0330",
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


def _docs():
    result = {}
    for symbol in ("MES", "MNQ"):
        result[market_path(symbol)] = _market(symbol)
        result[position_path(symbol)] = new_flat_position(symbol, NOW)
    return result


def test_run_evidence_is_written_after_plan_attempts():
    events = []
    docs = _docs()

    result = run_scheduled_brain_job(
        run_id="run-evidence",
        scheduled_run_time=NOW,
        generated_at=datetime(2026, 9, 8, 3, 30, 2, tzinfo=timezone.utc),
        read_runtime_json=lambda path: docs.get(path),
        read_control_json=lambda path: _rules(),
        read_control_text=lambda path: "strategy prompt",
        generate_plan=lambda symbol, **_: _plan(symbol),
        publish_runtime_json=lambda path, doc: events.append(("plan", path, doc)),
        publish_run_evidence=lambda path, doc: events.append(("evidence", path, doc)),
    )

    assert [event[1] for event in events[:2]] == [plan_path("MES"), plan_path("MNQ")]
    assert events[-1][0] == "evidence"
    assert events[-1][1] == brain_run_path("run-evidence")
    assert events[-1][2]["result"] == "published"
    assert result["evidence_published"] is True
    assert result["evidence_error"] is None


def test_evidence_write_failure_does_not_retract_valid_plan_publications():
    plan_writes = []
    docs = _docs()

    def fail_evidence(path, doc):
        raise RuntimeError("immutable evidence create failed")

    result = run_scheduled_brain_job(
        run_id="run-evidence-fail",
        scheduled_run_time=NOW,
        generated_at=datetime(2026, 9, 8, 3, 30, 2, tzinfo=timezone.utc),
        read_runtime_json=lambda path: docs.get(path),
        read_control_json=lambda path: _rules(),
        read_control_text=lambda path: "strategy prompt",
        generate_plan=lambda symbol, **_: _plan(symbol),
        publish_runtime_json=lambda path, doc: plan_writes.append(path),
        publish_run_evidence=fail_evidence,
    )

    assert plan_writes == [plan_path("MES"), plan_path("MNQ")]
    assert result["result"] == "published"
    assert result["evidence_published"] is False
    assert "evidence create failed" in result["evidence_error"]
