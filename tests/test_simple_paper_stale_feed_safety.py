from datetime import datetime, timezone

from trading_core.simple_paper_position_execution import manage_open_position


def test_stale_open_position_halts_without_fabricating_stop_fill():
    now = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    position = {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-stale-test",
        "position_version": 4,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 100.0,
        "stop_loss": 96.0,
        "take_profit": 110.0,
        "active_plan_id": "plan-old",
        "active_plan_generated_at": "2026-09-08T01:00:00Z",
        "action_valid_until": "2026-09-08T01:45:00Z",
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": "2026-09-08T01:00:00Z",
        "updated_at": "2026-09-08T01:00:00Z",
        "closed_at": None,
        "last_action_id": None,
    }
    account = {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10_000_000.0,
        "realized_pnl_usd": 0.0,
        "balance_usd": 10_000_000.0,
        "updated_at": "2026-09-08T01:00:00Z",
        "last_execution_id": None,
    }
    market = {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": "2026-09-08T01:00:00Z",
        "bars": [
            {
                "end": "2026-09-08T01:00:00Z",
                "high": 103.0,
                "low": 100.0,
                "close": 101.0,
            }
        ],
    }

    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=market,
        executed_at=now,
        cycle_id="cycle-stale",
        account=account,
        point_value=5.0,
        stale_after_minutes=15,
    )

    assert result["outcome"] == "NO_ACTION"
    assert result["stale_feed"] is True
    assert result["synthetic"] is False
    assert result.get("execution") is None
    assert result["position"] == position
    assert result["account"] == account
