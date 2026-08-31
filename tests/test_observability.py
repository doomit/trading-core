from datetime import datetime, timezone

import pytest

from trading_core.observability import (
    PIPELINE_STAGES,
    STAGE_STATES,
    make_activity,
    project_current_status,
    validate_activity,
)


NOW_MS = 1788148200000
NOW_ISO = datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def activity(stage, *, status="SUCCESS", event_id="evt-1", activity_id=None, error=None, occurred_at=NOW_ISO):
    return make_activity(
        activity_id=activity_id or f"a-{stage.lower()}",
        occurred_at=occurred_at,
        actor_type="AZURE",
        actor_name="test",
        stage=stage,
        event_type=f"{stage}_TEST",
        status=status,
        event_id=event_id,
        correlation_id=event_id,
        error=error,
        producer_version="test-v1",
    )


def healthy_market():
    return {
        "ingest_healthy": True,
        "build_healthy": True,
        "symbols": {
            "MES": {
                "latest_1m": "2026-08-31T03:49:00Z",
                "latest_5m": "2026-08-31T03:45:00Z",
                "last_received": "2026-08-31T03:50:00Z",
                "features_as_of": "2026-08-31T03:45:00Z",
                "healthy": True,
            },
            "MNQ": {
                "latest_1m": "2026-08-31T03:49:00Z",
                "latest_5m": "2026-08-31T03:45:00Z",
                "last_received": "2026-08-31T03:50:00Z",
                "features_as_of": "2026-08-31T03:45:00Z",
                "healthy": True,
            },
        },
    }


def test_stage_and_state_vocabulary_is_exact_and_ordered():
    assert PIPELINE_STAGES == (
        "MARKET_INGEST",
        "MARKET_BUILD",
        "EVENT_DETECT",
        "BRAIN_REQUEST",
        "BRAIN_DECIDE",
        "PLAN_VALIDATE",
        "RISK_GATE",
        "ORDER_EXECUTE",
        "POSITION_MANAGE",
        "TRADE_COMPLETE",
    )
    assert STAGE_STATES == frozenset({"HEALTHY", "ACTIVE", "IDLE", "WAITING", "BLOCKED", "ERROR"})


def test_make_activity_normalizes_material_activity():
    doc = activity("BRAIN_DECIDE")
    assert doc["schema"] == "trading_activity_v1"
    assert doc["activity_id"] == "a-brain_decide"
    assert doc["actor_type"] == "AZURE"
    assert doc["stage"] == "BRAIN_DECIDE"
    assert doc["event_id"] == "evt-1"
    assert doc["correlation_id"] == "evt-1"
    assert doc["producer_version"] == "test-v1"
    assert validate_activity(doc) == (None, {})


def test_validate_activity_rejects_secret_shaped_context_recursively():
    doc = activity("BRAIN_DECIDE")
    doc["context"] = {"safe": "ok", "nested": {"Authorization": "Bearer should-never-be-logged"}}
    error, details = validate_activity(doc)
    assert error == "secret_field_forbidden"
    assert details["path"] == "context.nested.Authorization"


@pytest.mark.parametrize("bad_stage", ["BRAIN", "EXECUTOR_RECEIVED", "", None])
def test_validate_activity_rejects_noncanonical_stage_names(bad_stage):
    doc = activity("BRAIN_DECIDE")
    doc["stage"] = bad_stage
    error, _ = validate_activity(doc)
    assert error == "invalid_stage"


def test_no_current_event_keeps_event_lifecycle_idle_not_error():
    status = project_current_status(
        activities=[],
        now_ms=NOW_MS,
        market=healthy_market(),
        paper={"equity_usd": 50000.0, "daily_pnl_usd": 0.0, "trade_count": 0},
        position={"side": "FLAT", "quantity": 0},
        projection={"watermark": 10, "source": "azure"},
    )
    assert status["schema"] == "trading_system_status_v1"
    assert status["overall"] == "HEALTHY"
    assert [x["stage"] for x in status["pipeline"]] == list(PIPELINE_STAGES)
    assert status["pipeline"][0]["status"] == "HEALTHY"
    assert status["pipeline"][1]["status"] == "HEALTHY"
    assert all(x["status"] == "IDLE" for x in status["pipeline"][2:])
    assert status["current_event"] is None
    assert status["attention"] is None


def test_upstream_error_marks_dependent_stages_blocked_and_attention_points_to_failure():
    activities = [
        activity("EVENT_DETECT", activity_id="a1"),
        activity("BRAIN_REQUEST", activity_id="a2"),
        activity(
            "BRAIN_DECIDE",
            status="ERROR",
            activity_id="a3",
            error={
                "code": "PLAN_NOT_PRODUCED",
                "severity": "ERROR",
                "first_seen_at": NOW_ISO,
                "last_seen_at": NOW_ISO,
                "recoverable": True,
                "summary": "Brain did not produce a plan before deadline",
            },
        ),
    ]
    status = project_current_status(
        activities=activities,
        now_ms=NOW_MS,
        market=healthy_market(),
        paper={"equity_usd": 50000.0, "daily_pnl_usd": 0.0, "trade_count": 0},
        position={"side": "FLAT", "quantity": 0},
        projection={"watermark": 11, "source": "azure"},
    )
    by_stage = {x["stage"]: x for x in status["pipeline"]}
    assert status["overall"] == "ERROR"
    assert status["current_event"]["event_id"] == "evt-1"
    assert by_stage["EVENT_DETECT"]["status"] == "HEALTHY"
    assert by_stage["BRAIN_REQUEST"]["status"] == "HEALTHY"
    assert by_stage["BRAIN_DECIDE"]["status"] == "ERROR"
    assert by_stage["BRAIN_DECIDE"]["error"]["code"] == "PLAN_NOT_PRODUCED"
    for stage in ("PLAN_VALIDATE", "RISK_GATE", "ORDER_EXECUTE", "POSITION_MANAGE", "TRADE_COMPLETE"):
        assert by_stage[stage]["status"] == "BLOCKED"
        assert by_stage[stage]["blocked_by"] == "BRAIN_DECIDE"
    assert status["attention"]["stage"] == "BRAIN_DECIDE"
    assert status["attention"]["code"] == "PLAN_NOT_PRODUCED"


def test_completed_event_is_not_reported_as_current_event():
    activities = [
        activity("EVENT_DETECT", activity_id="a1"),
        activity("BRAIN_REQUEST", activity_id="a2"),
        activity("BRAIN_DECIDE", activity_id="a3"),
        activity("PLAN_VALIDATE", activity_id="a4"),
        activity("RISK_GATE", activity_id="a5"),
        activity("TRADE_COMPLETE", activity_id="a6"),
    ]
    status = project_current_status(
        activities=activities,
        now_ms=NOW_MS,
        market=healthy_market(),
        paper={"equity_usd": 50000.0},
        position={"side": "FLAT", "quantity": 0},
        projection={"watermark": 12},
    )
    assert status["current_event"] is None
    assert all(x["status"] == "IDLE" for x in status["pipeline"][2:])
