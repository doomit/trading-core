from trading_core.dashboard import build_dashboard_state, validate_dashboard


def paper(paused=False, kill=False):
    return {"equity_usd": 50000.0, "realized_pnl_usd": 0.0, "unrealized_pnl_usd": 0.0, "position": "FLAT", "trade_count": 0, "consecutive_losses": 0, "paused": paused, "kill_switch": kill}


def receipt(event_id, stage, at, source, status="PASS", plan_id=None, reason=None, symbol="MNQ"):
    value = {"schema":"runtime_activity_v1","receipt_id":f"{event_id}-{stage}","event_id":event_id,"stage":stage,"status":status,"occurred_at":at,"source":source,"symbol":symbol}
    if plan_id: value["plan_id"] = plan_id
    if reason: value["reason_code"] = reason
    return value


def test_unseen_subsystem_never_pretends_to_be_fresh():
    state = build_dashboard_state([], paper(), "2026-08-31T09:00:00Z")
    feed = state["subsystems"]["market_feed"]
    assert feed["updated_at"] is None
    assert feed["age_seconds"] is None
    assert feed["freshness"] == "UNSEEN"
    assert state["paper_ready"] is False
    validate_dashboard(state)


def test_stale_market_feed_degrades_and_blocks_readiness():
    activities = [receipt("feed-1", "EVENT_CREATED", "2026-08-31T08:58:00Z", "github_feed")]
    state = build_dashboard_state(activities, paper(), "2026-08-31T09:00:00Z")
    feed = state["subsystems"]["market_feed"]
    assert feed["age_seconds"] == 120
    assert feed["freshness"] == "STALE"
    assert feed["status"] == "DEGRADED"
    assert "market_feed" in state["readiness_blockers"]


def test_mes_fresh_does_not_mask_stale_mnq_feed():
    activities = [
        receipt("mes-feed", "EVENT_CREATED", "2026-08-31T08:59:30Z", "github_feed", symbol="MES"),
        receipt("mnq-feed", "EVENT_CREATED", "2026-08-31T08:57:00Z", "github_feed", symbol="MNQ"),
    ]
    state = build_dashboard_state(activities, paper(), "2026-08-31T09:00:00Z")
    assert state["feed_freshness"]["MES"]["freshness"] == "FRESH"
    assert state["feed_freshness"]["MNQ"]["freshness"] == "STALE"
    assert state["paper_ready"] is False
    assert "market_feed:MNQ" in state["readiness_blockers"]
    validate_dashboard(state)


def test_missing_mnq_feed_is_an_explicit_symbol_blocker():
    activities = [receipt("mes-feed", "EVENT_CREATED", "2026-08-31T08:59:30Z", "github_feed", symbol="MES")]
    state = build_dashboard_state(activities, paper(), "2026-08-31T09:00:00Z")
    assert state["feed_freshness"]["MES"]["freshness"] == "FRESH"
    assert state["feed_freshness"]["MNQ"]["freshness"] == "UNSEEN"
    assert "market_feed:MNQ" in state["readiness_blockers"]
    validate_dashboard(state)


def test_pause_and_kill_are_explicit_readiness_blockers():
    state = build_dashboard_state([], paper(paused=True, kill=True), "2026-08-31T09:00:00Z")
    assert state["paper_ready"] is False
    assert "paper_paused" in state["readiness_blockers"]
    assert "kill_switch" in state["readiness_blockers"]


def test_stuck_event_and_risk_reject_remain_visible():
    activities = [
        receipt("stuck", "EVENT_CREATED", "2026-08-31T08:50:00Z", "azure_event_producer"),
        receipt("reject", "RISK_DECIDED", "2026-08-31T08:59:00Z", "risk_gateway", status="REJECTED", plan_id="p1", reason="MAX_DAILY_LOSS"),
        receipt("newer", "EVENT_CREATED", "2026-08-31T08:59:30Z", "azure_event_producer"),
    ]
    state = build_dashboard_state(activities, paper(), "2026-08-31T09:00:00Z")
    assert any(x["event_id"] == "stuck" and x["first_blocker_stage"] == "PR_COMMENT_CREATED" for x in state["stuck_work"])
    assert state["risk_rejects"][-1]["event_id"] == "reject"
    assert state["risk_rejects"][-1]["reason_code"] == "MAX_DAILY_LOSS"
    validate_dashboard(state)
