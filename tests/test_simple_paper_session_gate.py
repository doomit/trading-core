from __future__ import annotations

from datetime import datetime, timezone

from trading_core.simple_paper_position_execution import (
    execute_flat_plan,
    manage_open_position,
    new_flat_position,
)


NOW = datetime(2026, 9, 11, 20, 0, tzinfo=timezone.utc)


def _account():
    return {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10000000.0,
        "realized_pnl_usd": 0.0,
        "balance_usd": 10000000.0,
        "updated_at": "2026-09-11T19:50:00Z",
        "last_execution_id": None,
    }


def _market(*, high=103.0, low=99.0, close=101.0):
    return {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": "2026-09-11T19:59:00Z",
        "bars": [
            {
                "end": "2026-09-11T19:59:00Z",
                "high": high,
                "low": low,
                "close": close,
            }
        ],
    }


def _open_plan():
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-open-after-gate",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-11T19:59:00Z",
        "generated_at": "2026-09-11T19:59:10Z",
        "action_valid_until": "2026-09-11T20:15:00Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "OPEN",
        "side": "LONG",
        "confidence": 0.9,
        "analysis_summary": ["gate test"],
        "entry": {"order_type": "MARKET", "trigger_price": None, "qty": 2},
        "protection": {"stop_loss": 95.0, "take_profit": 110.0},
        "add_once": None,
        "reduce_once": None,
    }


def _open_position():
    return {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-gate-test",
        "position_version": 4,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 100.0,
        "stop_loss": 95.0,
        "take_profit": 110.0,
        "active_plan_id": "plan-old",
        "active_plan_generated_at": "2026-09-11T19:30:00Z",
        "action_valid_until": "2026-09-11T20:15:00Z",
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": "2026-09-11T19:30:00Z",
        "updated_at": "2026-09-11T19:30:00Z",
        "closed_at": None,
        "last_action_id": None,
    }


def _update_with_add(position):
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-update-with-add",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-11T19:59:00Z",
        "generated_at": "2026-09-11T19:59:10Z",
        "action_valid_until": "2026-09-11T20:15:00Z",
        "target_position": {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": position["position_version"],
        },
        "decision": "UPDATE",
        "side": "LONG",
        "confidence": 0.9,
        "analysis_summary": ["tighten protection but do not add after gate"],
        "entry": None,
        "protection": {"stop_loss": 96.0, "take_profit": 111.0},
        "add_once": {"order_type": "MARKET", "trigger_price": None, "qty": 1},
        "reduce_once": None,
    }


def _exit_plan(position):
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-exit-after-gate",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-11T19:59:00Z",
        "generated_at": "2026-09-11T19:59:10Z",
        "action_valid_until": "2026-09-11T20:15:00Z",
        "target_position": {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": position["position_version"],
        },
        "decision": "EXIT",
        "side": "LONG",
        "confidence": 0.9,
        "analysis_summary": ["exit remains allowed"],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }


def test_flat_position_cannot_open_when_new_risk_is_blocked():
    flat = new_flat_position("MES", NOW)
    result = execute_flat_plan(
        current_position=flat,
        accepted_plan=_open_plan(),
        market=_market(),
        executed_at=NOW,
        cycle_id="cycle-block-open",
        account=_account(),
        position_id="MES-would-have-opened",
        point_value=5.0,
        allow_new_risk=False,
    )
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["status"] == "FLAT"
    assert result.get("execution") is None


def test_existing_pending_add_is_cancelled_when_gate_closes():
    position = _open_position()
    position["pending_add"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(),
        executed_at=NOW,
        cycle_id="cycle-block-pending-add",
        account=_account(),
        point_value=5.0,
        allow_new_risk=False,
    )
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["qty"] == 2
    assert result["position"]["pending_add"] is None
    assert result["position"]["position_version"] == 5
    assert result.get("execution") is None


def test_update_can_tighten_protection_but_cannot_arm_add_when_blocked():
    position = _open_position()
    result = manage_open_position(
        current_position=position,
        accepted_plan=_update_with_add(position),
        market=_market(),
        executed_at=NOW,
        cycle_id="cycle-update-block-add",
        account=_account(),
        point_value=5.0,
        allow_new_risk=False,
    )
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["stop_loss"] == 96.0
    assert result["position"]["take_profit"] == 111.0
    assert result["position"]["pending_add"] is None
    assert result["position"]["qty"] == 2


def test_protective_stop_still_closes_when_new_risk_is_blocked():
    position = _open_position()
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(low=94.0),
        executed_at=NOW,
        cycle_id="cycle-stop-after-gate",
        account=_account(),
        point_value=5.0,
        allow_new_risk=False,
    )
    assert result["outcome"] == "STOPPED"
    assert result["execution"]["action"] == "STOP"
    assert result["position"]["status"] == "FLAT"


def test_explicit_exit_still_closes_when_new_risk_is_blocked():
    position = _open_position()
    result = manage_open_position(
        current_position=position,
        accepted_plan=_exit_plan(position),
        market=_market(),
        executed_at=NOW,
        cycle_id="cycle-exit-after-gate",
        account=_account(),
        point_value=5.0,
        allow_new_risk=False,
    )
    assert result["outcome"] == "EXITED"
    assert result["execution"]["action"] == "EXIT"
    assert result["position"]["status"] == "FLAT"
