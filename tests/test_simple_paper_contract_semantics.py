from datetime import datetime, timezone

from trading_core.simple_paper_contracts import (
    candidate_plan_outcome,
    plan_latency_ms,
    position_ref,
)


def _position(version: int = 3) -> dict:
    return {
        "status": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": version,
    }


def _plan(version: int = 3) -> dict:
    return {
        "plan_id": "plan-mes-1015",
        "analysis_bar_end": "2026-09-07T17:15:00Z",
        "generated_at": "2026-09-07T17:15:20Z",
        "action_valid_until": "2026-09-07T17:31:00Z",
        "target_position": {
            "state": "OPEN",
            "position_id": "MES-20260907-0907-01",
            "position_version": version,
        },
    }


def test_position_ref_is_exact_for_open_and_has_no_fake_identity_for_flat():
    assert position_ref(_position()) == {
        "state": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": 3,
    }
    assert position_ref({"status": "FLAT", "position_id": None, "position_version": None}) == {
        "state": "FLAT",
        "position_id": None,
        "position_version": None,
    }


def test_candidate_plan_is_accepted_only_for_exact_current_position_version():
    now = datetime(2026, 9, 7, 17, 16, tzinfo=timezone.utc)
    assert candidate_plan_outcome(_plan(version=3), _position(version=3), now) == "ACCEPTED"
    assert candidate_plan_outcome(_plan(version=2), _position(version=3), now) == "IGNORED_POSITION_MISMATCH"


def test_flat_plan_does_not_match_an_open_position_and_open_plan_does_not_match_flat():
    now = datetime(2026, 9, 7, 17, 16, tzinfo=timezone.utc)
    flat_plan = _plan()
    flat_plan["target_position"] = {"state": "FLAT", "position_id": None, "position_version": None}
    assert candidate_plan_outcome(flat_plan, _position(), now) == "IGNORED_POSITION_MISMATCH"

    open_plan = _plan()
    flat_position = {"status": "FLAT", "position_id": None, "position_version": None}
    assert candidate_plan_outcome(open_plan, flat_position, now) == "IGNORED_POSITION_MISMATCH"


def test_expired_candidate_is_ignored_but_does_not_invalidate_previous_active_plan():
    now = datetime(2026, 9, 7, 17, 31, 1, tzinfo=timezone.utc)
    assert candidate_plan_outcome(_plan(), _position(), now) == "IGNORED_EXPIRED"


def test_already_observed_plan_is_a_noop_before_other_candidate_checks():
    now = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
    assert (
        candidate_plan_outcome(_plan(), _position(version=99), now, last_observed_plan_id="plan-mes-1015")
        == "ALREADY_OBSERVED"
    )


def test_plan_latency_uses_analysis_bar_generation_and_pull_timestamps():
    generated = "2026-09-07T17:15:20Z"
    analysis_bar_end = "2026-09-07T17:15:00Z"
    pulled_at = datetime(2026, 9, 7, 17, 15, 35, tzinfo=timezone.utc)

    assert plan_latency_ms(analysis_bar_end, generated, pulled_at) == (20000, 15000)
