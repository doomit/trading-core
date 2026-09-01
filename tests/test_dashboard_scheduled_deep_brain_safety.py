from trading_core.dashboard import build_dashboard_state, validate_dashboard


def paper():
    return {
        "equity_usd": 50000.0,
        "realized_pnl_usd": 0.0,
        "unrealized_pnl_usd": 0.0,
        "position": "FLAT",
        "trade_count": 0,
        "consecutive_losses": 0,
        "paused": False,
        "kill_switch": False,
    }


def test_malformed_scheduled_deep_brain_is_a_blocker_not_an_exception():
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "COMPLETE",
        "paper_only": True,
        "completed_at": "not-a-time",
        "next_expected_at": "also-not-a-time",
        "outputs": [],
    }

    state = build_dashboard_state(
        [], paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )

    assert state["paper_ready"] is False
    assert "scheduled_deep_brain" in state["readiness_blockers"]
    assert state["scheduled_deep_brain"]["freshness"] == "STALE"


def test_timezone_naive_scheduled_deep_brain_timestamp_fails_closed_not_with_exception():
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "COMPLETE",
        "paper_only": True,
        "completed_at": "2026-08-30T15:30:00",
        "next_expected_at": "2026-08-30T15:45:00-07:00",
        "outputs": [],
    }

    state = build_dashboard_state(
        [], paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )

    assert state["paper_ready"] is False
    assert "scheduled_deep_brain" in state["readiness_blockers"]
    assert state["scheduled_deep_brain"]["freshness"] == "STALE"
    assert state["scheduled_deep_brain"]["updated_at"] is None
    assert state["scheduled_deep_brain"]["age_seconds"] is None


def test_dashboard_v1_remains_backward_compatible_without_scheduled_deep_brain_property():
    state = build_dashboard_state([], paper(), "2026-08-30T15:31:00-07:00")
    state.pop("scheduled_deep_brain")

    validate_dashboard(state)


def test_fresh_running_scheduled_deep_brain_is_visible_without_adding_its_blocker():
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "RUNNING",
        "paper_only": True,
        "run_id": "run-2",
        "worker_id": "worker-2",
        "started_at": "2026-08-30T15:29:00-07:00",
        "lease_expires_at": "2026-08-30T15:35:00-07:00",
        "context_version": "ctx-2",
        "last_completed_context_version": "ctx-1",
        "outputs": [],
        "skipped_symbols": [],
    }

    state = build_dashboard_state(
        [], paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )

    assert state["scheduled_deep_brain"]["state"] == "RUNNING"
    assert state["scheduled_deep_brain"]["freshness"] == "FRESH"
    assert "scheduled_deep_brain" not in state["readiness_blockers"]
