from datetime import datetime, timedelta, timezone

from trading_core.event_orchestrator import PickupStatus, classify_plan_pickup


NOW = datetime(2026, 9, 3, 8, 37, tzinfo=timezone.utc)
EVENT_ID = "evt_brain_MES1_null_state_version_probe"


def test_explicit_null_expected_state_version_does_not_disable_binding():
    plan = {
        "schema": "trading_plan_v1",
        "plan_id": EVENT_ID,
        "trigger_event_id": EVENT_ID,
        "created_at": (NOW - timedelta(seconds=5)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=55)).isoformat(),
        "symbol": "MES1!",
        "decision": "NO_TRADE",
        "confidence": 0.8,
        "analysis_summary": ["legacy event must not bypass state-version binding"],
        "based_on_state_version": "ctx_unbound",
    }

    result = classify_plan_pickup(
        plan,
        expected_event_id=EVENT_ID,
        expected_symbol="MES1!",
        expected_state_version=None,
        now=NOW,
    )

    assert result.status is PickupStatus.REJECTED
    assert result.reason_code == "STATE_VERSION_MISMATCH"
    assert result.plan is None
