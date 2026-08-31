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


def test_render_dashboard_surfaces_operator_readiness_feed_stuck_and_risk_rejects():
    state = base_state()
    state.update(
        paper_ready=False,
        readiness_blockers=["market_feed:MNQ", "stuck_work"],
        feed_freshness={
            "MES": {
                "updated_at": "2026-08-30T15:45:50-07:00",
                "age_seconds": 10,
                "freshness": "FRESH",
            },
            "MNQ": {
                "updated_at": "2026-08-30T15:40:00-07:00",
                "age_seconds": 360,
                "freshness": "STALE",
            },
        },
        stuck_work=[
            {
                "event_id": "evt-stuck",
                "age_seconds": 420,
                "first_blocker_stage": "EXECUTOR_RECEIVED",
            }
        ],
        risk_rejects=[
            {
                "event_id": "evt-reject",
                "occurred_at": "2026-08-30T15:44:00-07:00",
                "reason_code": "DAILY_LOSS_LIMIT",
            }
        ],
    )

    markdown = render_dashboard(state)

    assert "Paper readiness" in markdown
    assert "NOT READY" in markdown
    assert "market_feed:MNQ" in markdown
    assert "Feed freshness" in markdown
    assert "MES" in markdown and "FRESH" in markdown and "10s" in markdown
    assert "MNQ" in markdown and "STALE" in markdown and "360s" in markdown
    assert "Stuck work" in markdown
    assert "evt-stuck" in markdown and "420s" in markdown
    assert "Recent risk rejects" in markdown
    assert "evt-reject" in markdown and "DAILY_LOSS_LIMIT" in markdown


def test_render_dashboard_shows_unseen_canonical_symbol_when_feed_entry_missing():
    state = base_state()
    state["feed_freshness"] = {
        "MES": {
            "updated_at": "2026-08-30T15:45:50-07:00",
            "age_seconds": 10,
            "freshness": "FRESH",
        }
    }

    markdown = render_dashboard(state)

    assert "| MES | ✅ FRESH | 10s | 2026-08-30T15:45:50-07:00 |" in markdown
    assert "| MNQ | ⏳ UNSEEN | — | — |" in markdown
