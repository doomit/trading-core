from trading_core.dashboard import build_dashboard_state, load_dashboard_schema, validate_dashboard


def receipt(event_id, stage, occurred_at, source, status="PASS", plan_id=None, reason_code=None, decision=None):
    item = {
        "schema": "runtime_activity_v1",
        "receipt_id": f"{event_id}-{stage}-{occurred_at.replace(':', '')}",
        "event_id": event_id,
        "stage": stage,
        "status": status,
        "occurred_at": occurred_at,
        "source": source,
        "symbol": "MES",
    }
    if plan_id:
        item["plan_id"] = plan_id
    if reason_code:
        item["reason_code"] = reason_code
    if decision:
        item["details"] = {"decision": decision}
    return item


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


def test_dashboard_schema_is_packaged_and_generated_state_validates():
    assert load_dashboard_schema()["$id"] == "trading_dashboard_v1.schema.json"
    state = build_dashboard_state([], paper(), "2026-08-30T15:00:14-07:00")
    validate_dashboard(state)


def test_completed_no_trade_has_no_blocker_and_computed_latency():
    activities = [
        receipt("evt-1", "EVENT_CREATED", "2026-08-30T15:00:00-07:00", "azure_event_producer"),
        receipt("evt-1", "PR_COMMENT_CREATED", "2026-08-30T15:00:02-07:00", "azure_event_producer"),
        receipt("evt-1", "BRAIN_TRIGGERED", "2026-08-30T15:00:05-07:00", "chatgpt_event_task"),
        receipt("evt-1", "PLAN_WRITTEN", "2026-08-30T15:00:09-07:00", "chatgpt_event_task", plan_id="plan-1", decision="NO_TRADE"),
        receipt("evt-1", "PLAN_VALIDATED", "2026-08-30T15:00:10-07:00", "github_validation", plan_id="plan-1"),
        receipt("evt-1", "EXECUTOR_RECEIVED", "2026-08-30T15:00:12-07:00", "azure_executor", plan_id="plan-1"),
        receipt("evt-1", "COMPLETED", "2026-08-30T15:00:13-07:00", "azure_executor", plan_id="plan-1", reason_code="NO_TRADE"),
    ]
    state = build_dashboard_state(activities, paper(), "2026-08-30T15:00:14-07:00")
    event = state["current_event"]
    assert event["decision"] == "NO_TRADE"
    assert event["current_stage"] == "COMPLETED"
    assert event["first_blocker_stage"] is None
    assert event["terminal_reason"] == "NO_TRADE"
    assert event["end_to_end_latency_ms"] == 13000
    validate_dashboard(state)


def test_risk_rejection_is_terminal_and_missing_prerequisite_is_detected():
    activities = [
        receipt("evt-2", "EVENT_CREATED", "2026-08-30T15:01:00-07:00", "azure_event_producer"),
        receipt("evt-2", "PR_COMMENT_CREATED", "2026-08-30T15:01:01-07:00", "azure_event_producer"),
        receipt("evt-2", "BRAIN_TRIGGERED", "2026-08-30T15:01:02-07:00", "chatgpt_event_task"),
        receipt("evt-2", "PLAN_WRITTEN", "2026-08-30T15:01:04-07:00", "chatgpt_event_task", plan_id="plan-2", decision="LONG"),
        receipt("evt-2", "EXECUTOR_RECEIVED", "2026-08-30T15:01:06-07:00", "azure_executor", plan_id="plan-2"),
        receipt("evt-2", "RISK_DECIDED", "2026-08-30T15:01:07-07:00", "risk_gateway", status="REJECTED", plan_id="plan-2", reason_code="MAX_DAILY_LOSS"),
    ]
    state = build_dashboard_state(activities, paper(), "2026-08-30T15:01:08-07:00")
    assert state["current_event"]["current_stage"] == "RISK_DECIDED"
    assert state["current_event"]["first_blocker_stage"] == "PLAN_VALIDATED"
    assert state["current_event"]["terminal_reason"] == "MAX_DAILY_LOSS"
    assert state["subsystems"]["paper_account"]["status"] == "HEALTHY"


def test_newest_event_wins_and_recent_activity_is_capped():
    activities = [
        receipt(f"evt-{i}", "EVENT_CREATED", f"2026-08-30T15:{i:02d}:00-07:00", "azure_event_producer")
        for i in range(25)
    ]
    state = build_dashboard_state(activities, paper(), "2026-08-30T15:30:00-07:00")
    assert state["current_event"]["event_id"] == "evt-24"
    assert len(state["recent_activity"]) == 20
    assert state["recent_activity"][0]["event_id"] == "evt-5"
    assert state["recent_activity"][-1]["event_id"] == "evt-24"


def test_stale_scheduled_deep_brain_blocks_paper_readiness_and_is_projected():
    heartbeat = {
        "schema": "deep_brain_status_v1",
        "state": "COMPLETE",
        "run_id": "deep-run-1",
        "worker_id": "agent_chatgpt_worker_3",
        "started_at": "2026-08-30T15:00:00-07:00",
        "completed_at": "2026-08-30T15:04:00-07:00",
        "context_version": "ctx-old",
        "last_completed_context_version": "ctx-old",
        "as_of_5m": 1788120000000,
        "next_expected_at": "2026-08-30T15:15:00-07:00",
        "outputs": [
            {
                "symbol": "MES1!",
                "thesis_id": "thesis-1",
                "plan_id": "plan-1",
                "analysis_id": "analysis-1",
                "context_version": "ctx-old",
            }
        ],
        "skipped_symbols": [{"symbol": "MNQ1!", "reason": "missing context"}],
        "paper_only": True,
    }

    state = build_dashboard_state(
        [],
        paper(),
        "2026-08-30T15:31:00-07:00",
        scheduled_deep_brain=heartbeat,
    )

    assert state["paper_ready"] is False
    assert "scheduled_deep_brain" in state["readiness_blockers"]
    assert state["scheduled_deep_brain"] == {
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
    }
