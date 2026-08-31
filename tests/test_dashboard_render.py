from trading_core.dashboard_render import render_dashboard


def base_state():
    subsystem = lambda status, summary: {
        "status": status,
        "updated_at": "2026-08-30T15:46:00-07:00",
        "summary": summary,
    }
    return {
        "schema": "trading_dashboard_v1",
        "generated_at": "2026-08-30T15:46:00-07:00",
        "overall_status": "DEGRADED",
        "subsystems": {
            "market_feed": subsystem("HEALTHY", "MES/MNQ live feed healthy"),
            "azure_event_producer": subsystem("HEALTHY", "3m producer active"),
            "chatgpt_brain": subsystem("HEALTHY", "event task healthy"),
            "azure_executor": subsystem("WAITING", "handoff not yet observed"),
            "paper_account": subsystem("WAITING", "paper runtime warming up"),
        },
        "current_event": {
            "event_id": "evt-123",
            "plan_id": "evt-123",
            "symbol": "MES",
            "decision": "NO_TRADE",
            "current_stage": "PLAN_VALIDATED",
            "first_blocker_stage": "EXECUTOR_RECEIVED",
            "terminal_reason": None,
            "end_to_end_latency_ms": None,
        },
        "paper": {
            "equity_usd": 50000.0,
            "realized_pnl_usd": 125.5,
            "unrealized_pnl_usd": -12.25,
            "position": "FLAT",
            "trade_count": 2,
            "consecutive_losses": 0,
            "paused": False,
            "kill_switch": False,
        },
        "recent_activity": [],
    }


def test_render_dashboard_shows_subsystems_event_progress_and_first_blocker():
    markdown = render_dashboard(base_state())
    assert "Trading E2E Dashboard" in markdown
    assert "Azure Event Producer" in markdown
    assert "ChatGPT Brain" in markdown
    assert "Paper Account" in markdown
    assert "evt-123" in markdown
    assert "PLAN_VALIDATED" in markdown
    assert "EXECUTOR_RECEIVED" in markdown
    assert "First blocker" in markdown
    assert "$50,000.00" in markdown
    assert "$125.50" in markdown


def test_render_dashboard_caps_activity_at_20_newest_rows():
    state = base_state()
    state["recent_activity"] = [
        {
            "schema": "runtime_activity_v1",
            "receipt_id": f"r-{i}",
            "event_id": f"evt-{i}",
            "stage": "EVENT_CREATED",
            "status": "INFO",
            "occurred_at": f"2026-08-30T15:{i:02d}:00-07:00",
            "source": "control_plane",
            "reason_code": f"R{i}",
        }
        for i in range(25)
    ]
    markdown = render_dashboard(state)
    assert "evt-24" in markdown
    assert "evt-5" in markdown
    assert "evt-4" not in markdown


def test_render_dashboard_handles_no_active_event():
    state = base_state()
    state["current_event"] = None
    markdown = render_dashboard(state)
    assert "No active correlated event" in markdown
