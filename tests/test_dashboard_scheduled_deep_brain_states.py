import pytest

from trading_core.dashboard import build_dashboard_state


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


@pytest.mark.parametrize(
    ("heartbeat", "expected_freshness"),
    [
        (
            {
                "schema": "deep_brain_status_v1",
                "state": "FAILED",
                "paper_only": True,
                "completed_at": "2026-08-30T15:30:00-07:00",
                "context_version": "ctx-1",
                "outputs": [],
                "skipped_symbols": [],
            },
            "STALE",
        ),
        (
            {
                "schema": "deep_brain_status_v1",
                "state": "RUNNING",
                "paper_only": True,
                "started_at": "2026-08-30T15:20:00-07:00",
                "lease_expires_at": "2026-08-30T15:25:00-07:00",
                "context_version": "ctx-2",
                "outputs": [],
                "skipped_symbols": [],
            },
            "STALE",
        ),
    ],
)
def test_failed_and_lease_expired_running_are_readiness_blockers(heartbeat, expected_freshness):
    state = build_dashboard_state(
        [], paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )
    assert state["scheduled_deep_brain"]["freshness"] == expected_freshness
    assert "scheduled_deep_brain" in state["readiness_blockers"]


def test_fresh_complete_does_not_add_scheduled_deep_brain_blocker():
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "COMPLETE",
        "paper_only": True,
        "completed_at": "2026-08-30T15:29:00-07:00",
        "next_expected_at": "2026-08-30T15:45:00-07:00",
        "context_version": "ctx-3",
        "last_completed_context_version": "ctx-3",
        "outputs": [],
        "skipped_symbols": [],
    }
    state = build_dashboard_state(
        [], paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )
    assert state["scheduled_deep_brain"]["freshness"] == "FRESH"
    assert "scheduled_deep_brain" not in state["readiness_blockers"]
