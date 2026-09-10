from datetime import datetime, timezone

from trading_core.simple_paper_eod import eod_close_due, force_eod_close
from trading_core.simple_paper_position_execution import manage_open_position


def _position():
    return {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-20260910-1554-01",
        "position_version": 2,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 100.0,
        "stop_loss": 90.0,
        "take_profit": 120.0,
        "active_plan_id": "plan-old",
        "active_plan_generated_at": "2026-09-10T19:45:00Z",
        "action_valid_until": "2026-09-10T20:15:00Z",
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": "2026-09-10T19:00:00Z",
        "updated_at": "2026-09-10T19:45:00Z",
        "closed_at": None,
        "last_action_id": None,
    }


def _account():
    return {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10000000.0,
        "realized_pnl_usd": 0.0,
        "balance_usd": 10000000.0,
        "updated_at": "2026-09-10T19:45:00Z",
        "last_execution_id": None,
    }


def _market(close=103.0, end="2026-09-10T19:55:00Z"):
    return {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": end,
        "bars": [{"end": end, "high": 104.0, "low": 99.0, "close": close}],
    }


def test_eod_due_uses_1555_america_new_york_boundary():
    # 2026-09-10 is EDT (UTC-4): 15:55 ET == 19:55 UTC.
    assert eod_close_due(datetime(2026, 9, 10, 19, 54, 59, tzinfo=timezone.utc)) is False
    assert eod_close_due(datetime(2026, 9, 10, 19, 55, 0, tzinfo=timezone.utc)) is True


def test_eod_due_remains_dst_aware_in_winter():
    # 2026-12-07 is EST (UTC-5): 15:55 ET == 20:55 UTC.
    assert eod_close_due(datetime(2026, 12, 7, 20, 54, 59, tzinfo=timezone.utc)) is False
    assert eod_close_due(datetime(2026, 12, 7, 20, 55, 0, tzinfo=timezone.utc)) is True


def test_eod_close_is_deterministic_paper_execution_at_latest_close():
    result = force_eod_close(
        current_position=_position(),
        market=_market(close=103.0),
        executed_at=datetime(2026, 9, 10, 19, 55, 1, tzinfo=timezone.utc),
        cycle_id="cycle-eod",
        account=_account(),
        point_value=5.0,
    )
    assert result is not None
    assert result["outcome"] == "EOD_FORCED_CLOSE"
    assert result["execution"]["action"] == "EOD_FORCED_CLOSE"
    assert result["execution"]["price"] == 103.0
    assert result["execution"]["position_version_before"] == 2
    assert result["execution"]["position_version_after"] == 3
    assert result["account"]["realized_pnl_usd"] == 30.0
    assert result["position"]["status"] == "FLAT"


def test_explicit_exit_plan_closes_at_latest_close_when_protection_not_hit():
    position = _position()
    exit_plan = {
        "schema": "trading_plan_v2",
        "plan_id": "plan-exit",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-10T19:49:00Z",
        "generated_at": "2026-09-10T19:49:10Z",
        "action_valid_until": "2026-09-10T20:15:00Z",
        "target_position": {"state": "OPEN", "position_id": position["position_id"], "position_version": 2},
        "decision": "EXIT",
        "side": "LONG",
        "confidence": 0.9,
        "analysis_summary": ["exit test"],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }
    result = manage_open_position(
        current_position=position,
        accepted_plan=exit_plan,
        market={
            "schema": "market_snapshot_v1",
            "symbol": "MES",
            "latest_bar_end": "2026-09-10T19:49:00Z",
            "bars": [{"end": "2026-09-10T19:49:00Z", "high": 104.0, "low": 99.0, "close": 103.0}],
        },
        executed_at=datetime(2026, 9, 10, 19, 49, 15, tzinfo=timezone.utc),
        cycle_id="cycle-exit",
        account=_account(),
        point_value=5.0,
    )
    assert result["outcome"] == "EXITED"
    assert result["execution"]["action"] == "EXIT"
    assert result["execution"]["price"] == 103.0
    assert result["position"]["status"] == "FLAT"
