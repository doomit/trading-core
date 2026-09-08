from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_core.simple_paper_position_execution import (
    apply_open_plan_update,
    commit_paper_transition,
    execute_flat_plan,
    manage_open_position,
    new_flat_position,
)


NOW = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)


def _open(*, symbol="MES", side="LONG", qty=2, version=4, stop=99.0, tp=110.0):
    return {
        "schema": "position_state_v1",
        "symbol": symbol,
        "status": "OPEN",
        "position_id": f"{symbol}-20260907-1800-01",
        "position_version": version,
        "side": side,
        "qty": qty,
        "avg_entry": 100.0,
        "stop_loss": stop,
        "take_profit": tp,
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


def _account(realized=0.0):
    return {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10000000.0,
        "realized_pnl_usd": realized,
        "balance_usd": 10000000.0 + realized,
        "updated_at": "2026-09-08T01:00:00Z",
        "last_execution_id": None,
    }


def _market(*, high=105.0, low=95.0, close=101.0, end="2026-09-08T01:29:00Z"):
    return {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": end,
        "bars": [{"end": end, "high": high, "low": low, "close": close}],
    }


def _update_plan(position, *, stop=98.0, tp=112.0, add=None, reduce=None, valid="2026-09-08T01:45:00Z"):
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-update",
        "symbol": position["symbol"],
        "analysis_bar_end": "2026-09-08T01:15:00Z",
        "generated_at": "2026-09-08T01:15:20Z",
        "action_valid_until": valid,
        "target_position": {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": position["position_version"],
        },
        "decision": "UPDATE",
        "side": position["side"],
        "confidence": 0.8,
        "analysis_summary": ["test"],
        "entry": None,
        "protection": {"stop_loss": stop, "take_profit": tp},
        "add_once": add,
        "reduce_once": reduce,
    }


def _open_plan(symbol="MES", *, qty=2, order_type="MARKET", trigger=None):
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-open",
        "symbol": symbol,
        "analysis_bar_end": "2026-09-08T01:29:00Z",
        "generated_at": "2026-09-08T01:29:20Z",
        "action_valid_until": "2026-09-08T01:45:00Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "OPEN",
        "side": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["test"],
        "entry": {"order_type": order_type, "trigger_price": trigger, "qty": qty},
        "protection": {"stop_loss": 95.0, "take_profit": 110.0},
        "add_once": None,
        "reduce_once": None,
    }


def test_protection_update_increments_version_and_replaces_pending_actions():
    position = _open()
    plan = _update_plan(
        position,
        stop=97.0,
        tp=113.0,
        add={"order_type": "LIMIT", "trigger_price": 98.0, "qty": 1},
        reduce={"order_type": "LIMIT", "trigger_price": 108.0, "qty": 1},
    )

    updated = apply_open_plan_update(position, plan, NOW)

    assert updated["position_version"] == 5
    assert updated["stop_loss"] == 97.0
    assert updated["take_profit"] == 113.0
    assert updated["pending_add"]["qty"] == 1
    assert updated["pending_reduce"]["qty"] == 1
    assert updated["active_plan_id"] == "plan-update"


def test_update_rejects_add_that_could_exceed_six_contracts():
    position = _open(qty=6)
    plan = _update_plan(position, add={"order_type": "MARKET", "trigger_price": None, "qty": 1})
    with pytest.raises(ValueError, match="maximum 6"):
        apply_open_plan_update(position, plan, NOW)


def test_same_bar_stop_and_take_profit_is_adverse_first():
    position = _open(stop=95.0, tp=105.0)
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(high=106.0, low=94.0, close=101.0),
        executed_at=NOW,
        cycle_id="cycle-stop-first",
        account=_account(),
        point_value=5.0,
    )
    assert result["outcome"] == "STOPPED"
    assert result["execution"]["action"] == "STOP"
    assert result["execution"]["price"] == 95.0
    assert result["position"]["status"] == "FLAT"
    assert result["execution"]["position_version_before"] == 4
    assert result["execution"]["position_version_after"] == 5
    assert result["account"]["realized_pnl_usd"] == -50.0


def test_stale_feed_forced_stop_is_synthetic_at_durable_stop():
    position = _open(stop=96.0, tp=110.0)
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(end="2026-09-08T01:00:00Z", high=103.0, low=100.0),
        executed_at=NOW,
        cycle_id="cycle-stale",
        account=_account(),
        point_value=5.0,
        stale_after_minutes=15,
    )
    assert result["outcome"] == "STALE_FEED_FORCED_STOP"
    assert result["synthetic"] is True
    assert result["execution"]["action"] == "STALE_FEED_FORCED_STOP"
    assert result["execution"]["price"] == 96.0


def test_expired_pending_actions_are_removed_but_protection_remains():
    position = _open()
    position["pending_add"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    position["pending_reduce"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    position["action_valid_until"] = "2026-09-08T01:20:00Z"
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(high=104.0, low=100.0),
        executed_at=NOW,
        cycle_id="cycle-expired",
        account=_account(),
        point_value=5.0,
    )
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["position_version"] == 5
    assert result["position"]["pending_add"] is None
    assert result["position"]["pending_reduce"] is None
    assert result["position"]["stop_loss"] == 99.0
    assert result["position"]["take_profit"] == 110.0


def test_reduce_is_one_shot_realizes_pnl_and_precedes_add_when_both_trigger():
    position = _open(qty=3, stop=90.0, tp=120.0)
    position["pending_reduce"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    position["pending_add"] = {"order_type": "MARKET", "trigger_price": None, "qty": 1}
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(high=105.0, low=95.0, close=102.0),
        executed_at=NOW,
        cycle_id="cycle-reduce",
        account=_account(),
        point_value=5.0,
    )
    assert result["outcome"] == "REDUCED"
    assert result["position"]["qty"] == 2
    assert result["position"]["pending_reduce"] is None
    assert result["position"]["pending_add"] is not None
    assert result["position"]["position_version"] == 5
    assert result["execution"]["action"] == "REDUCE"
    assert result["account"]["realized_pnl_usd"] == 10.0


def test_add_is_one_shot_and_updates_weighted_average_entry():
    position = _open(qty=2, stop=90.0, tp=120.0)
    position["pending_add"] = {"order_type": "LIMIT", "trigger_price": 98.0, "qty": 1}
    result = manage_open_position(
        current_position=position,
        accepted_plan=None,
        market=_market(high=103.0, low=97.0, close=101.0),
        executed_at=NOW,
        cycle_id="cycle-add",
        account=_account(),
        point_value=5.0,
    )
    assert result["outcome"] == "ADDED"
    assert result["position"]["qty"] == 3
    assert result["position"]["avg_entry"] == pytest.approx((100.0 * 2 + 98.0) / 3)
    assert result["position"]["pending_add"] is None
    assert result["execution"]["realized_pnl_usd"] == 0.0


def test_historical_bar_before_analysis_end_never_opens_new_position():
    flat = new_flat_position("MES", NOW)
    plan = _open_plan()
    result = execute_flat_plan(
        current_position=flat,
        accepted_plan=plan,
        market=_market(end="2026-09-08T01:20:00Z", high=110.0, low=90.0, close=101.0),
        executed_at=NOW,
        cycle_id="cycle-historical",
        account=_account(),
        position_id="MES-20260907-1830-01",
        point_value=5.0,
    )
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["status"] == "FLAT"


def test_valid_flat_open_creates_execution_and_account_without_realized_pnl():
    flat = new_flat_position("MES", NOW)
    result = execute_flat_plan(
        current_position=flat,
        accepted_plan=_open_plan(qty=2),
        market=_market(close=101.25),
        executed_at=NOW,
        cycle_id="cycle-open",
        account=_account(),
        position_id="MES-20260907-1830-01",
        point_value=5.0,
    )
    assert result["outcome"] == "OPENED"
    assert result["position"]["position_version"] == 0
    assert result["execution"]["action"] == "OPEN"
    assert result["execution"]["position_version_before"] is None
    assert result["execution"]["position_version_after"] == 0
    assert result["account"]["balance_usd"] == 10000000.0


def test_commit_writes_execution_then_account_then_position_and_mirror_failure_does_not_rollback():
    flat = new_flat_position("MES", NOW)
    transition = execute_flat_plan(
        current_position=flat,
        accepted_plan=_open_plan(qty=1),
        market=_market(close=101.0),
        executed_at=NOW,
        cycle_id="cycle-commit",
        account=_account(),
        position_id="MES-20260907-1830-01",
        point_value=5.0,
    )
    events = []

    def mirror(position):
        events.append("mirror")
        raise RuntimeError("github unavailable")

    committed = commit_paper_transition(
        transition,
        execution_exists=lambda execution_id: False,
        append_execution=lambda execution: events.append("execution"),
        save_account=lambda account: events.append("account"),
        save_position=lambda position: events.append("position"),
        mirror_position=mirror,
    )
    assert committed is True
    assert events == ["execution", "account", "position", "mirror"]


def test_commit_is_idempotent_when_execution_already_exists():
    flat = new_flat_position("MES", NOW)
    transition = execute_flat_plan(
        current_position=flat,
        accepted_plan=_open_plan(qty=1),
        market=_market(close=101.0),
        executed_at=NOW,
        cycle_id="cycle-idempotent",
        account=_account(),
        position_id="MES-20260907-1830-01",
        point_value=5.0,
    )
    events = []
    committed = commit_paper_transition(
        transition,
        execution_exists=lambda execution_id: True,
        append_execution=lambda execution: events.append("execution"),
        save_account=lambda account: events.append("account"),
        save_position=lambda position: events.append("position"),
        mirror_position=lambda position: events.append("mirror"),
    )
    assert committed is False
    assert events == []
