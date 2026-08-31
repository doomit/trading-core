from datetime import datetime, timedelta, timezone

import pytest

from trading_core.event_orchestrator import (
    EventIdentityConflict,
    EventRecord,
    PickupStatus,
    PlanReservationStatus,
    canonical_plan_hash,
    classify_plan_pickup,
    expected_plan_id,
    reserve_plan_once,
    start_or_resume_event,
    validate_trading_plan,
)

NOW = datetime(2026, 8, 30, 23, 5, tzinfo=timezone.utc)


def valid_plan(event_id="evt_mes_20260830T230500Z_0001", **overrides):
    plan = {
        "schema": "trading_plan_v1",
        "plan_id": event_id,
        "trigger_event_id": event_id,
        "created_at": (NOW - timedelta(seconds=5)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=55)).isoformat(),
        "symbol": "MES1!",
        "decision": "NO_TRADE",
        "confidence": 0.8,
        "analysis_summary": ["No setup with sufficient edge."],
    }
    plan.update(overrides)
    return plan


class FakeRepository:
    def __init__(self):
        self.events = {}
        self.plan_hashes = {}

    def get_event(self, event_id):
        return self.events.get(event_id)

    def create_event(self, record):
        if record.event_id in self.events:
            return False
        self.events[record.event_id] = record
        return True

    def get_plan_hash(self, plan_id):
        return self.plan_hashes.get(plan_id)

    def create_plan_reservation(self, plan_id, plan_hash):
        if plan_id in self.plan_hashes:
            return False
        self.plan_hashes[plan_id] = plan_hash
        return True


def test_event_identity_is_safe_and_deterministic():
    event_id = "evt_mes_20260830T230500Z_0001"
    assert expected_plan_id(event_id) == event_id
    for bad in ("", "has space", "slash/bad", "../escape", "x" * 161):
        with pytest.raises(ValueError):
            expected_plan_id(bad)


def test_missing_plan_waits_and_valid_plan_is_ready():
    event_id = "evt_mes_20260830T230500Z_0001"
    waiting = classify_plan_pickup(None, expected_event_id=event_id, now=NOW)
    assert waiting.status is PickupStatus.WAITING
    assert waiting.reason_code == "PLAN_NOT_AVAILABLE"

    plan = valid_plan(event_id, decision="HOLD")
    ready = classify_plan_pickup(plan, expected_event_id=event_id, now=NOW)
    assert ready.status is PickupStatus.READY
    assert ready.reason_code == "PLAN_READY"
    assert ready.plan == plan


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"schema": "wrong"}, "INVALID_PLAN_SCHEMA"),
        ({"trigger_event_id": "evt_other"}, "EVENT_ID_MISMATCH"),
        ({"plan_id": "evt_other"}, "PLAN_ID_MISMATCH"),
        ({"decision": "BUY"}, "INVALID_PLAN_SCHEMA"),
        ({"confidence": True}, "INVALID_PLAN_SCHEMA"),
        ({"based_on_state_version": True}, "INVALID_PLAN_SCHEMA"),
        ({"created_at": "2026-08-30T23:04:55"}, "INVALID_PLAN_TIME"),
        ({"valid_until": "2026-08-30T23:05:55"}, "INVALID_PLAN_TIME"),
        (
            {"created_at": (NOW + timedelta(seconds=10)).isoformat(), "valid_until": (NOW + timedelta(seconds=60)).isoformat()},
            "PLAN_CREATED_IN_FUTURE",
        ),
        (
            {"created_at": (NOW - timedelta(seconds=10)).isoformat(), "valid_until": (NOW - timedelta(seconds=1)).isoformat()},
            "PLAN_EXPIRED",
        ),
    ],
)
def test_invalid_or_stale_plan_fails_closed(changes, reason):
    event_id = "evt_mes_20260830T230500Z_0001"
    result = classify_plan_pickup(valid_plan(event_id, **changes), expected_event_id=event_id, now=NOW)
    assert result.status is PickupStatus.REJECTED
    assert result.reason_code == reason
    assert result.plan is None


@pytest.mark.parametrize("state_version", [7, "snapshot-7", None])
def test_based_on_state_version_accepts_only_canonical_schema_types(state_version):
    event_id = "evt_mes_20260830T230500Z_0001"
    result = classify_plan_pickup(
        valid_plan(event_id, based_on_state_version=state_version),
        expected_event_id=event_id,
        now=NOW,
    )
    assert result.status is PickupStatus.READY


def test_symbol_binding_fails_closed():
    event_id = "evt_mes_20260830T230500Z_0001"
    result = classify_plan_pickup(
        valid_plan(event_id, symbol="MNQ1!"),
        expected_event_id=event_id,
        now=NOW,
        expected_symbol="MES1!",
    )
    assert result.status is PickupStatus.REJECTED
    assert result.reason_code == "SYMBOL_MISMATCH"


def test_duplicate_event_resumes_same_identity_but_different_symbol_conflicts():
    repo = FakeRepository()
    event_id = "evt_mes_20260830T230500Z_0002"
    original_deadline = NOW + timedelta(seconds=90)
    first, created = start_or_resume_event(repo, event_id=event_id, symbol="MES1!", created_at=NOW, deadline=original_deadline)
    resumed, created_again = start_or_resume_event(
        repo,
        event_id=event_id,
        symbol="MES1!",
        created_at=NOW + timedelta(seconds=15),
        deadline=NOW + timedelta(seconds=120),
    )
    assert created is True
    assert created_again is False
    assert resumed == first
    assert resumed.deadline == original_deadline

    with pytest.raises(EventIdentityConflict):
        start_or_resume_event(
            repo,
            event_id=event_id,
            symbol="MNQ1!",
            created_at=NOW + timedelta(seconds=20),
            deadline=NOW + timedelta(seconds=130),
        )


def test_plan_reservation_is_exactly_once_and_hash_conflict_fails_closed():
    repo = FakeRepository()
    plan = valid_plan("evt_mes_20260830T230500Z_0003")
    first = reserve_plan_once(repo, plan)
    second = reserve_plan_once(repo, plan)
    assert first.status is PlanReservationStatus.RESERVED
    assert second.status is PlanReservationStatus.DUPLICATE
    assert second.plan_hash == first.plan_hash == canonical_plan_hash(plan)

    changed = dict(plan)
    changed["confidence"] = 0.5
    conflict = reserve_plan_once(repo, changed)
    assert conflict.status is PlanReservationStatus.CONFLICT
    assert conflict.reason_code == "PLAN_ID_HASH_CONFLICT"


def test_event_record_requires_aware_ordered_times():
    with pytest.raises(ValueError):
        EventRecord(
            event_id="evt_mes_20260830T230500Z_0004",
            plan_id="evt_mes_20260830T230500Z_0004",
            symbol="MES1!",
            created_at=datetime(2026, 8, 30, 23, 5),
            deadline=NOW + timedelta(seconds=90),
        )
    with pytest.raises(ValueError):
        EventRecord(
            event_id="evt_mes_20260830T230500Z_0004",
            plan_id="evt_mes_20260830T230500Z_0004",
            symbol="MES1!",
            created_at=NOW,
            deadline=NOW,
        )


def test_validate_trading_plan_accepts_canonical_plan():
    event_id = "evt_mes_20260830T230500Z_0005"
    validate_trading_plan(valid_plan(event_id), expected_event_id=event_id, now=NOW, expected_symbol="MES1!")
