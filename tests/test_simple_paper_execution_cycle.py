from __future__ import annotations

from datetime import datetime, timezone

import pytest

from trading_core.simple_paper_execution_cycle import run_symbol_execution_cycle


def _flat(symbol: str = "MES") -> dict:
    return {
        "schema": "position_state_v1",
        "symbol": symbol,
        "status": "FLAT",
        "position_id": None,
        "position_version": None,
        "qty": 0,
    }


def _open(symbol: str = "MES", *, version: int = 4) -> dict:
    return {
        "schema": "position_state_v1",
        "symbol": symbol,
        "status": "OPEN",
        "position_id": f"{symbol}-20260907-1800-01",
        "position_version": version,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 7700.0,
        "stop_loss": 7690.0,
        "take_profit": 7720.0,
        "active_plan_id": "plan-old",
    }


def _plan_for(position: dict, *, plan_id: str = "plan-mes-1815", decision: str = "HOLD") -> dict:
    if position["status"] == "FLAT":
        target = {"state": "FLAT", "position_id": None, "position_version": None}
    else:
        target = {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": position["position_version"],
        }
    return {
        "schema": "trading_plan_v2",
        "plan_id": plan_id,
        "symbol": position["symbol"],
        "analysis_bar_end": "2026-09-08T01:14:00Z",
        "generated_at": "2026-09-08T01:14:20Z",
        "action_valid_until": "2026-09-08T01:30:00Z",
        "target_position": target,
        "decision": decision,
        "side": "LONG" if decision == "OPEN" else None,
        "entry": {"order_type": "MARKET", "trigger_price": None, "qty": 1} if decision == "OPEN" else None,
        "protection": {"stop_loss": 7690.0, "take_profit": 7720.0} if decision == "OPEN" else None,
        "add_once": None,
        "reduce_once": None,
    }


def _market(symbol: str = "MES", *, latest_bar_end: str = "2026-09-08T01:14:00Z") -> dict:
    return {
        "schema": "market_snapshot_v1",
        "symbol": symbol,
        "latest_bar_end": latest_bar_end,
        "bars": [{"end": latest_bar_end, "high": 7712.0, "low": 7708.0, "close": 7710.0}],
    }


def test_cycle_uses_exact_order_and_exact_plan_path_for_open_position():
    events: list[str] = []
    position = _open()
    plan = _plan_for(position)

    def load_position(symbol: str) -> dict:
        events.append("position")
        assert symbol == "MES"
        return position

    def maybe_eod_close(current: dict, tick_at: datetime):
        events.append("eod")
        return None

    def read_plan(path: str):
        events.append(f"plan:{path}")
        return plan

    def write_plan_observation(log: dict) -> None:
        events.append(f"plan_log:{log['outcome']}")
        assert log["candidate_plan_id"] == plan["plan_id"]
        assert log["current_position"]["position_version"] == 4

    def load_market(symbol: str) -> dict:
        events.append("market")
        return _market(symbol)

    def manage_open(current: dict, accepted_plan: dict | None, market: dict | None, tick_at: datetime) -> dict:
        events.append("manage_open")
        assert accepted_plan is plan
        assert market["latest_bar_end"] == "2026-09-08T01:14:00Z"
        return {"position": current, "outcome": "NO_ACTION", "synthetic": False, "stale_feed": False}

    def execute_flat(*args, **kwargs):
        raise AssertionError("OPEN state must not use FLAT execution")

    def write_cycle_log(log: dict) -> None:
        events.append(f"cycle_log:{log['outcome']}")
        assert log["plan_id"] == plan["plan_id"]
        assert log["market_age_ms"] == 60000

    result = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-181500",
        tick_at=datetime(2026, 9, 8, 1, 15, tzinfo=timezone.utc),
        load_position=load_position,
        maybe_eod_close=maybe_eod_close,
        read_plan=read_plan,
        write_plan_observation=write_plan_observation,
        load_market=load_market,
        manage_open_position=manage_open,
        execute_flat=execute_flat,
        write_cycle_log=write_cycle_log,
    )

    assert result["outcome"] == "NO_ACTION"
    assert events == [
        "position",
        "eod",
        "plan:runtime/simple-paper/plan/MES/current.json",
        "plan_log:ACCEPTED",
        "market",
        "manage_open",
        "cycle_log:NO_ACTION",
    ]


def test_plan_read_failure_is_logged_but_never_blocks_open_management():
    events: list[str] = []
    position = _open()

    def read_plan(path: str):
        events.append("plan_read")
        raise RuntimeError("github unavailable")

    def write_plan_observation(log: dict) -> None:
        events.append(f"plan_log:{log['outcome']}")
        assert log["outcome"] == "READ_FAILED"
        assert "github unavailable" in log["error"]

    def manage_open(current: dict, accepted_plan: dict | None, market: dict | None, tick_at: datetime) -> dict:
        events.append("manage_open")
        assert accepted_plan is None
        return {"position": current, "outcome": "NO_ACTION", "synthetic": False, "stale_feed": False}

    result = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-read-fail",
        tick_at=datetime(2026, 9, 8, 1, 15, tzinfo=timezone.utc),
        load_position=lambda symbol: position,
        maybe_eod_close=lambda current, tick: None,
        read_plan=read_plan,
        write_plan_observation=write_plan_observation,
        load_market=lambda symbol: _market(symbol),
        manage_open_position=manage_open,
        execute_flat=lambda *args, **kwargs: pytest.fail("must not execute FLAT path"),
        write_cycle_log=lambda log: events.append(f"cycle_log:{log['outcome']}"),
    )

    assert result["outcome"] == "NO_ACTION"
    assert events == ["plan_read", "plan_log:READ_FAILED", "manage_open", "cycle_log:NO_ACTION"]


def test_eod_close_short_circuits_symbol_before_plan_pull_and_market_read():
    position = _open()
    flat = _flat()
    called = {"plan": False, "market": False, "manage": False}
    logs: list[dict] = []

    def eod_close(current: dict, tick_at: datetime) -> dict:
        return {
            "position": flat,
            "outcome": "EOD_FORCED_CLOSE",
            "synthetic": False,
            "stale_feed": False,
        }

    result = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-eod",
        tick_at=datetime(2026, 9, 8, 22, 1, tzinfo=timezone.utc),
        load_position=lambda symbol: position,
        maybe_eod_close=eod_close,
        read_plan=lambda path: called.__setitem__("plan", True),
        write_plan_observation=lambda log: None,
        load_market=lambda symbol: called.__setitem__("market", True),
        manage_open_position=lambda *args: called.__setitem__("manage", True),
        execute_flat=lambda *args: called.__setitem__("manage", True),
        write_cycle_log=logs.append,
    )

    assert result["outcome"] == "EOD_FORCED_CLOSE"
    assert called == {"plan": False, "market": False, "manage": False}
    assert logs[0]["eod_forced"] is True
    assert logs[0]["end_position"]["state"] == "FLAT"


def test_stale_market_is_passed_into_open_management_not_used_as_upstream_gate():
    position = _open()
    stale_market = _market(latest_bar_end="2026-09-08T00:40:00Z")
    managed = {"called": False}

    def manage_open(current: dict, accepted_plan: dict | None, market: dict | None, tick_at: datetime) -> dict:
        managed["called"] = True
        assert market is stale_market
        return {
            "position": _flat(),
            "outcome": "STALE_FEED_FORCED_STOP",
            "synthetic": True,
            "stale_feed": True,
        }

    result = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-stale",
        tick_at=datetime(2026, 9, 8, 1, 15, tzinfo=timezone.utc),
        load_position=lambda symbol: position,
        maybe_eod_close=lambda current, tick: None,
        read_plan=lambda path: None,
        write_plan_observation=lambda log: None,
        load_market=lambda symbol: stale_market,
        manage_open_position=manage_open,
        execute_flat=lambda *args: pytest.fail("must not execute FLAT path"),
        write_cycle_log=lambda log: None,
    )

    assert managed["called"] is True
    assert result["outcome"] == "STALE_FEED_FORCED_STOP"
    assert result["stale_feed"] is True


def test_flat_executes_only_a_new_accepted_plan_and_duplicate_plan_is_noop():
    flat = _flat()
    plan = _plan_for(flat, plan_id="plan-flat-open", decision="OPEN")
    opened = {"count": 0}

    def execute_flat(current: dict, accepted_plan: dict | None, market: dict | None, tick_at: datetime) -> dict:
        opened["count"] += 1
        assert accepted_plan is plan
        return {
            "position": _open(version=0),
            "outcome": "OPENED",
            "synthetic": False,
            "stale_feed": False,
        }

    first = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-flat-1",
        tick_at=datetime(2026, 9, 8, 1, 15, tzinfo=timezone.utc),
        load_position=lambda symbol: flat,
        maybe_eod_close=lambda current, tick: None,
        read_plan=lambda path: plan,
        write_plan_observation=lambda log: None,
        load_market=lambda symbol: _market(symbol),
        manage_open_position=lambda *args: pytest.fail("must not manage OPEN path"),
        execute_flat=execute_flat,
        write_cycle_log=lambda log: None,
        last_observed_plan_id=None,
    )
    second = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-flat-2",
        tick_at=datetime(2026, 9, 8, 1, 15, 15, tzinfo=timezone.utc),
        load_position=lambda symbol: flat,
        maybe_eod_close=lambda current, tick: None,
        read_plan=lambda path: plan,
        write_plan_observation=lambda log: None,
        load_market=lambda symbol: _market(symbol),
        manage_open_position=lambda *args: pytest.fail("must not manage OPEN path"),
        execute_flat=lambda *args: pytest.fail("duplicate observed plan must not execute again"),
        write_cycle_log=lambda log: None,
        last_observed_plan_id=plan["plan_id"],
    )

    assert first["outcome"] == "OPENED"
    assert second["outcome"] == "NO_ACTION"
    assert opened["count"] == 1
