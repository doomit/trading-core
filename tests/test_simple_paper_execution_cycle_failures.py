from datetime import datetime, timezone

from trading_core.simple_paper_execution_cycle import run_symbol_execution_cycle


TICK = datetime(2026, 9, 8, 1, 30, 0, tzinfo=timezone.utc)


def _open(version=4):
    return {
        "schema": "position_state_v1",
        "symbol": "MES",
        "status": "OPEN",
        "position_id": "MES-20260907-1800-01",
        "position_version": version,
        "side": "LONG",
        "qty": 2,
        "avg_entry": 7700.0,
        "stop_loss": 7690.0,
        "take_profit": 7720.0,
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


def _hold_plan(position, *, version=None, valid_until="2026-09-08T01:45:00Z"):
    target_version = position["position_version"] if version is None else version
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-hold",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-08T01:29:00Z",
        "generated_at": "2026-09-08T01:29:10Z",
        "action_valid_until": valid_until,
        "target_position": {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": target_version,
        },
        "decision": "HOLD",
        "side": "LONG",
        "confidence": 0.7,
        "analysis_summary": ["hold"],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }


def _market():
    return {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": "2026-09-08T01:29:00Z",
        "bars": [{"end": "2026-09-08T01:29:00Z", "high": 7712.0, "low": 7708.0, "close": 7710.0}],
    }


def _run(position, plan, *, load_market=None):
    observations = []
    managed = []
    cycle_logs = []

    def manage(current, accepted, market, tick):
        managed.append((accepted, market))
        return {"position": current, "outcome": "NO_ACTION", "synthetic": False, "stale_feed": market is None}

    result = run_symbol_execution_cycle(
        symbol="MES",
        cycle_id="cycle-failure",
        tick_at=TICK,
        load_position=lambda symbol: position,
        maybe_eod_close=lambda current, tick: None,
        read_plan=lambda path: plan,
        write_plan_observation=observations.append,
        load_market=load_market or (lambda symbol: _market()),
        manage_open_position=manage,
        execute_flat=lambda *args: (_ for _ in ()).throw(AssertionError("OPEN must not use FLAT path")),
        write_cycle_log=cycle_logs.append,
    )
    return result, observations, managed, cycle_logs


def test_position_mismatched_plan_is_ignored_but_open_position_is_managed():
    position = _open(version=4)
    result, observations, managed, _ = _run(position, _hold_plan(position, version=3))
    assert observations[0]["outcome"] == "IGNORED_POSITION_MISMATCH"
    assert managed[0][0] is None
    assert managed[0][1] is not None
    assert result["outcome"] == "NO_ACTION"


def test_expired_plan_is_ignored_but_durable_open_position_is_managed():
    position = _open()
    result, observations, managed, _ = _run(
        position,
        _hold_plan(position, valid_until="2026-09-08T01:29:59Z"),
    )
    assert observations[0]["outcome"] == "IGNORED_EXPIRED"
    assert managed[0][0] is None
    assert result["outcome"] == "NO_ACTION"


def test_malformed_plan_is_logged_invalid_without_blocking_open_management():
    position = _open()
    malformed = _hold_plan(position)
    del malformed["generated_at"]
    result, observations, managed, _ = _run(position, malformed)
    assert observations[0]["outcome"] == "INVALID"
    assert observations[0]["error"]
    assert managed[0][0] is None
    assert result["outcome"] == "NO_ACTION"


def test_market_read_failure_still_enters_open_management_with_no_price():
    position = _open()

    def fail_market(symbol):
        raise RuntimeError("azure table unavailable")

    result, observations, managed, cycle_logs = _run(position, _hold_plan(position), load_market=fail_market)
    assert observations[0]["outcome"] == "ACCEPTED"
    assert managed[0][1] is None
    assert result["stale_feed"] is True
    assert "azure table unavailable" in cycle_logs[0]["error"]


def test_non_object_plan_is_invalid_without_blocking_open_management():
    position = _open()
    result, observations, managed, cycle_logs = _run(position, ["corrupt-plan"])
    assert observations[0]["outcome"] == "INVALID"
    assert observations[0]["candidate_plan_id"] is None
    assert observations[0]["error"]
    assert managed[0][0] is None
    assert managed[0][1] is not None
    assert result["outcome"] == "NO_ACTION"
    assert cycle_logs[0]["outcome"] == "NO_ACTION"
