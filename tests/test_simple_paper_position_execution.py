from datetime import datetime, timezone

from trading_core.simple_paper_position_execution import open_position_from_plan


def test_flat_market_open_creates_version_zero_with_durable_protection():
    flat = {
        "schema": "position_state_v1",
        "symbol": "MES",
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
        "updated_at": "2026-09-08T01:10:00Z",
        "closed_at": None,
        "last_action_id": None,
    }
    plan = {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-1815",
        "symbol": "MES",
        "generated_at": "2026-09-08T01:15:20Z",
        "action_valid_until": "2026-09-08T01:31:00Z",
        "decision": "OPEN",
        "side": "LONG",
        "entry": {"order_type": "MARKET", "trigger_price": None, "qty": 2},
        "protection": {"stop_loss": 6510.0, "take_profit": 6530.0},
        "add_once": None,
        "reduce_once": None,
    }

    position = open_position_from_plan(
        current_position=flat,
        plan=plan,
        fill_price=6520.25,
        executed_at=datetime(2026, 9, 8, 1, 16, tzinfo=timezone.utc),
        position_id="MES-20260907-1816-01",
    )

    assert position["status"] == "OPEN"
    assert position["position_id"] == "MES-20260907-1816-01"
    assert position["position_version"] == 0
    assert position["qty"] == 2
    assert position["avg_entry"] == 6520.25
    assert position["stop_loss"] == 6510.0
    assert position["take_profit"] == 6530.0
    assert position["active_plan_id"] == "plan-mes-1815"
    assert position["pending_add"] is None
    assert position["pending_reduce"] is None
    assert position["last_action_id"] == "plan-mes-1815:open"
