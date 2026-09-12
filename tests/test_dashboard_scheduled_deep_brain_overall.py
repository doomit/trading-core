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


def activity(receipt_id, source, symbol=None):
    item = {
        "schema": "runtime_activity_v1",
        "receipt_id": receipt_id,
        "event_id": receipt_id,
        "stage": "EVENT_CREATED",
        "status": "PASS",
        "occurred_at": "2026-08-30T15:30:30-07:00",
        "source": source,
    }
    if symbol:
        item["symbol"] = symbol
    return item


def test_stale_scheduled_deep_brain_degrades_overall_health_even_when_other_subsystems_are_healthy():
    activities = [
        activity("feed-mes", "github_feed", "MES"),
        activity("feed-mnq", "github_feed", "MNQ"),
        activity("producer", "azure_event_producer"),
        activity("event-brain", "chatgpt_event_task"),
        activity("executor", "azure_executor"),
        activity("paper", "paper_broker"),
    ]
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "COMPLETE",
        "paper_only": True,
        "completed_at": "2026-08-30T15:04:00-07:00",
        "next_expected_at": "2026-08-30T15:15:00-07:00",
        "context_version": "ctx-old",
        "last_completed_context_version": "ctx-old",
        "outputs": [],
        "skipped_symbols": [],
    }

    state = build_dashboard_state(
        activities, paper(), "2026-08-30T15:31:00-07:00", scheduled_deep_brain=heartbeat
    )

    assert state["readiness_blockers"] == ["scheduled_deep_brain"]
    assert state["overall_status"] == "DEGRADED"
