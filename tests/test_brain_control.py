from datetime import datetime, timezone

from trading_core.brain_control import (
    DispatchAction,
    evaluate_anomaly_condition,
    decide_brain_dispatch,
    resolve_effective_plan,
    price_action_tags,
)

UTC = timezone.utc


def test_structured_anomaly_condition_cross_above():
    condition = {"metric": "Close", "comparator": "CROSS_ABOVE", "threshold": 101.0}
    assert evaluate_anomaly_condition(condition, {"Close": 102.0}, {"Close": 100.0}) is True
    assert evaluate_anomaly_condition(condition, {"Close": 102.0}, {"Close": 101.5}) is False


def test_medium_deviation_waits_when_deep_brain_is_close():
    decision = decide_brain_dispatch(
        severity="MEDIUM",
        requested_tier="L1",
        minutes_to_next_deep=1.5,
        scheduler_state="IDLE",
        event_inflight=False,
        events_this_hour=2,
        is_5m_close=True,
        emergency=False,
    )
    assert decision.action is DispatchAction.WAIT_FOR_DEEP


def test_material_invalidation_dispatches_when_next_deep_is_far():
    decision = decide_brain_dispatch(
        severity="HIGH",
        requested_tier="L2",
        minutes_to_next_deep=8.0,
        scheduler_state="IDLE",
        event_inflight=False,
        events_this_hour=2,
        is_5m_close=True,
        emergency=False,
    )
    assert decision.action is DispatchAction.DISPATCH_EVENT
    assert decision.tier == "L2"
    assert decision.freeze_new_entries is True


def test_inflight_event_coalesces_instead_of_dispatching_second_brain():
    decision = decide_brain_dispatch(
        severity="CRITICAL",
        requested_tier="L3",
        minutes_to_next_deep=8,
        scheduler_state="IDLE",
        event_inflight=True,
        events_this_hour=1,
        is_5m_close=True,
        emergency=True,
    )
    assert decision.action is DispatchAction.COALESCE
    assert decision.freeze_new_entries is True


def test_hard_budget_blocks_non_emergency_event():
    decision = decide_brain_dispatch(
        severity="HIGH",
        requested_tier="L2",
        minutes_to_next_deep=10,
        scheduler_state="LATE",
        event_inflight=False,
        events_this_hour=10,
        is_5m_close=True,
        emergency=False,
    )
    assert decision.action is DispatchAction.WAIT_FOR_DEEP


def test_emergency_can_bypass_soft_budget_but_not_create_parallel_event():
    decision = decide_brain_dispatch(
        severity="CRITICAL",
        requested_tier="L3",
        minutes_to_next_deep=1,
        scheduler_state="RUNNING",
        event_inflight=False,
        events_this_hour=9,
        is_5m_close=False,
        emergency=True,
    )
    assert decision.action is DispatchAction.DISPATCH_EVENT
    assert decision.freeze_new_entries is True


def test_override_wins_only_when_bound_to_active_thesis_and_not_expired():
    now = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    baseline = {
        "plan_id": "base-1",
        "plan_role": "BASELINE",
        "baseline_thesis_id": "thesis-1",
        "symbol": "MES1!",
        "created_at": "2026-08-31T21:55:00+00:00",
        "valid_until": "2026-08-31T22:20:00+00:00",
    }
    override = {
        "plan_id": "over-1",
        "plan_role": "OVERRIDE",
        "baseline_thesis_id": "thesis-1",
        "symbol": "MES1!",
        "created_at": "2026-08-31T21:59:00+00:00",
        "valid_until": "2026-08-31T22:05:00+00:00",
    }
    result = resolve_effective_plan(
        baseline=baseline,
        override=override,
        active_thesis_id="thesis-1",
        symbol="MES1!",
        now=now,
        replan_pending=False,
    )
    assert result.plan["plan_id"] == "over-1"
    assert result.allow_new_entries is True


def test_replan_pending_freezes_new_entries_but_keeps_effective_plan_for_position_management():
    now = datetime(2026, 8, 31, 22, 0, tzinfo=UTC)
    baseline = {
        "plan_id": "base-1",
        "plan_role": "BASELINE",
        "baseline_thesis_id": "thesis-1",
        "symbol": "MES1!",
        "created_at": "2026-08-31T21:55:00+00:00",
        "valid_until": "2026-08-31T22:20:00+00:00",
    }
    result = resolve_effective_plan(
        baseline=baseline,
        override=None,
        active_thesis_id="thesis-1",
        symbol="MES1!",
        now=now,
        replan_pending=True,
    )
    assert result.plan["plan_id"] == "base-1"
    assert result.allow_new_entries is False


def test_price_action_tags_are_causal_and_use_existing_features():
    previous = {
        "Close": 100.0,
        "High": 101.0,
        "Low": 99.0,
        "EMA20": 100.5,
        "OR15High": 103.0,
        "OR15Low": 97.0,
    }
    current = {
        "Open": 100.2,
        "High": 104.0,
        "Low": 100.0,
        "Close": 103.8,
        "EMA20": 101.0,
        "ATR14": 2.0,
        "OR15High": 103.0,
        "OR15Low": 97.0,
        "BodyPctOfRange": 0.8,
        "CloseLocation": 0.95,
        "MicroPathEfficiency": 0.75,
        "MicroEndRun": 3,
    }
    tags = price_action_tags(current, previous)
    assert "OR_BREAKOUT_UP" in tags
    assert "STRONG_BULL_BODY" in tags
    assert "ABOVE_EMA20" in tags
