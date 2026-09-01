from trading_core.dashboard_render import render_dashboard


def test_render_dashboard_surfaces_scheduled_deep_brain_separately_from_event_brain():
    state = {
        "schema": "trading_dashboard_v1",
        "generated_at": "2026-08-30T15:31:00-07:00",
        "overall_status": "DEGRADED",
        "paper_ready": False,
        "readiness_blockers": ["scheduled_deep_brain"],
        "feed_freshness": {},
        "scheduled_deep_brain": {
            "state": "COMPLETE",
            "freshness": "STALE",
            "context_version": "ctx-old",
            "last_completed_context_version": "ctx-old",
            "updated_at": "2026-08-30T15:04:00-07:00",
            "next_expected_at": "2026-08-30T15:15:00-07:00",
            "age_seconds": 1620,
            "run_id": "deep-run-1",
            "worker_id": "agent_chatgpt_worker_3",
            "outputs_count": 1,
            "skipped_symbols": ["MNQ1!"],
        },
        "subsystems": {
            key: {"status": "HEALTHY", "updated_at": None, "summary": "ok"}
            for key in ("market_feed", "azure_event_producer", "chatgpt_brain", "azure_executor", "paper_account")
        },
        "current_event": None,
        "stuck_work": [],
        "risk_rejects": [],
        "paper": {
            "equity_usd": 50000.0,
            "realized_pnl_usd": 0.0,
            "unrealized_pnl_usd": 0.0,
            "position": "FLAT",
            "trade_count": 0,
            "consecutive_losses": 0,
            "paused": False,
            "kill_switch": False,
        },
        "recent_activity": [],
    }

    markdown = render_dashboard(state)

    assert "Scheduled Deep Brain" in markdown
    assert "COMPLETE" in markdown
    assert "STALE" in markdown
    assert "ctx-old" in markdown
    assert "1620s" in markdown
    assert "deep-run-1" in markdown
    assert "agent_chatgpt_worker_3" in markdown
    assert "MNQ1!" in markdown
