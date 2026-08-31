from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

_TIERS = {"L0", "L1", "L2", "L3"}
_SEVERITIES = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
_SCHEDULER_STATES = {"IDLE", "RUNNING", "COMPLETE", "LATE", "UNKNOWN"}


class DispatchAction(str, Enum):
    NO_EVENT = "NO_EVENT"
    WAIT_FOR_DEEP = "WAIT_FOR_DEEP"
    DISPATCH_EVENT = "DISPATCH_EVENT"
    COALESCE = "COALESCE"


@dataclass(frozen=True)
class BrainDispatchDecision:
    action: DispatchAction
    tier: str | None
    reason_code: str
    freeze_new_entries: bool = False


@dataclass(frozen=True)
class EffectivePlanDecision:
    plan: dict[str, Any] | None
    source: str | None
    allow_new_entries: bool
    reason_code: str


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    return float(value)


def evaluate_anomaly_condition(
    condition: dict[str, Any],
    current: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> bool:
    """Evaluate one safe structured thesis condition against causal feature snapshots."""
    if not isinstance(condition, dict):
        raise ValueError("condition must be an object")
    metric = condition.get("metric")
    comparator = condition.get("comparator")
    if not isinstance(metric, str) or not metric:
        raise ValueError("condition metric is required")
    if comparator not in {"GT", "GTE", "LT", "LTE", "CROSS_ABOVE", "CROSS_BELOW"}:
        raise ValueError("unsupported anomaly comparator")
    if metric not in current:
        return False

    lhs = _number(current[metric], metric)
    threshold = condition.get("threshold")
    if isinstance(threshold, dict):
        other = threshold.get("metric")
        if not isinstance(other, str) or other not in current:
            return False
        rhs = _number(current[other], other)
        prev_rhs = (
            None
            if previous is None or other not in previous
            else _number(previous[other], other)
        )
    else:
        rhs = _number(threshold, "threshold")
        prev_rhs = rhs

    if comparator == "GT":
        return lhs > rhs
    if comparator == "GTE":
        return lhs >= rhs
    if comparator == "LT":
        return lhs < rhs
    if comparator == "LTE":
        return lhs <= rhs
    if previous is None or metric not in previous:
        return False
    prev_lhs = _number(previous[metric], metric)
    if prev_rhs is None:
        return False
    if comparator == "CROSS_ABOVE":
        return prev_lhs <= prev_rhs and lhs > rhs
    return prev_lhs >= prev_rhs and lhs < rhs


def decide_brain_dispatch(
    *,
    severity: str,
    requested_tier: str,
    minutes_to_next_deep: float,
    scheduler_state: str,
    event_inflight: bool,
    events_this_hour: int,
    is_5m_close: bool,
    emergency: bool,
    soft_cap: int = 8,
    hard_cap: int = 10,
) -> BrainDispatchDecision:
    """Choose Event Brain versus waiting for the next scheduled Deep Brain."""
    if severity not in _SEVERITIES:
        raise ValueError("invalid severity")
    if requested_tier not in _TIERS:
        raise ValueError("invalid tier")
    if scheduler_state not in _SCHEDULER_STATES:
        raise ValueError("invalid scheduler_state")
    if events_this_hour < 0 or soft_cap < 0 or hard_cap <= soft_cap:
        raise ValueError("invalid event budget")

    eta = max(0.0, float(minutes_to_next_deep))
    freeze = emergency or _SEVERITIES[severity] >= _SEVERITIES["HIGH"]

    if severity == "NONE" or requested_tier == "L0":
        return BrainDispatchDecision(
            DispatchAction.NO_EVENT, None, "NO_MATERIAL_DEVIATION", False
        )
    if event_inflight:
        return BrainDispatchDecision(
            DispatchAction.COALESCE,
            requested_tier,
            "EVENT_BRAIN_ALREADY_INFLIGHT",
            freeze,
        )
    if events_this_hour >= hard_cap and not emergency:
        return BrainDispatchDecision(
            DispatchAction.WAIT_FOR_DEEP, None, "EVENT_HARD_BUDGET", freeze
        )
    if emergency:
        if events_this_hour >= hard_cap + 1:
            return BrainDispatchDecision(
                DispatchAction.WAIT_FOR_DEEP, None, "EMERGENCY_BUDGET_GUARD", True
            )
        return BrainDispatchDecision(
            DispatchAction.DISPATCH_EVENT, requested_tier, "EMERGENCY_REPLAN", True
        )
    if not is_5m_close and _SEVERITIES[severity] < _SEVERITIES["HIGH"]:
        return BrainDispatchDecision(
            DispatchAction.WAIT_FOR_DEEP, None, "PREFER_5M_CLOSE", False
        )
    if eta <= 2.0 and scheduler_state != "LATE":
        return BrainDispatchDecision(
            DispatchAction.WAIT_FOR_DEEP, None, "DEEP_BRAIN_IMMINENT", freeze
        )
    if events_this_hour >= soft_cap and _SEVERITIES[severity] < _SEVERITIES["CRITICAL"]:
        return BrainDispatchDecision(
            DispatchAction.WAIT_FOR_DEEP, None, "EVENT_SOFT_BUDGET", freeze
        )
    if _SEVERITIES[severity] >= _SEVERITIES["HIGH"] and (
        eta > 5.0 or scheduler_state == "LATE"
    ):
        return BrainDispatchDecision(
            DispatchAction.DISPATCH_EVENT, requested_tier, "THESIS_INVALIDATED", True
        )
    if severity == "MEDIUM" and eta > 5.0:
        return BrainDispatchDecision(
            DispatchAction.DISPATCH_EVENT, requested_tier, "MATERIAL_DEVIATION", False
        )
    return BrainDispatchDecision(
        DispatchAction.WAIT_FOR_DEEP, None, "DEFER_TO_DEEP_BRAIN", freeze
    )


def _aware(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        return None
    return parsed


def _applicable(
    plan: dict[str, Any] | None,
    *,
    role: str,
    active_thesis_id: str,
    symbol: str,
    now: datetime,
) -> bool:
    if not isinstance(plan, dict):
        return False
    if (
        plan.get("plan_role") != role
        or plan.get("baseline_thesis_id") != active_thesis_id
        or plan.get("symbol") != symbol
    ):
        return False
    created = _aware(plan.get("created_at"))
    valid_until = _aware(plan.get("valid_until"))
    return (
        created is not None
        and valid_until is not None
        and created <= now < valid_until
    )


def resolve_effective_plan(
    *,
    baseline: dict[str, Any] | None,
    override: dict[str, Any] | None,
    active_thesis_id: str,
    symbol: str,
    now: datetime,
    replan_pending: bool,
) -> EffectivePlanDecision:
    """Resolve immutable baseline/override inputs without last-writer-wins races."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if _applicable(
        override,
        role="OVERRIDE",
        active_thesis_id=active_thesis_id,
        symbol=symbol,
        now=now,
    ):
        return EffectivePlanDecision(
            dict(override), "OVERRIDE", not replan_pending, "ACTIVE_OVERRIDE"
        )
    if _applicable(
        baseline,
        role="BASELINE",
        active_thesis_id=active_thesis_id,
        symbol=symbol,
        now=now,
    ):
        return EffectivePlanDecision(
            dict(baseline), "BASELINE", not replan_pending, "ACTIVE_BASELINE"
        )
    return EffectivePlanDecision(None, None, False, "NO_APPLICABLE_PLAN")


def price_action_tags(
    current: dict[str, Any], previous: dict[str, Any] | None = None
) -> list[str]:
    """Produce small causal setup tags from already-built market features."""
    tags: list[str] = []
    close = current.get("Close")
    ema = current.get("EMA20")
    if (
        isinstance(close, (int, float))
        and not isinstance(close, bool)
        and isinstance(ema, (int, float))
        and not isinstance(ema, bool)
    ):
        tags.append(
            "ABOVE_EMA20" if close > ema else "BELOW_EMA20" if close < ema else "AT_EMA20"
        )

    body = current.get("BodyPctOfRange")
    close_location = current.get("CloseLocation")
    if isinstance(body, (int, float)) and isinstance(close_location, (int, float)):
        if body >= 0.65 and close_location >= 0.75:
            tags.append("STRONG_BULL_BODY")
        elif body >= 0.65 and close_location <= 0.25:
            tags.append("STRONG_BEAR_BODY")

    if previous:
        or_high = current.get("OR15High")
        or_low = current.get("OR15Low")
        previous_close = previous.get("Close")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (close, or_high, previous_close)
        ) and previous_close <= or_high < close:
            tags.append("OR_BREAKOUT_UP")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (close, or_low, previous_close)
        ) and previous_close >= or_low > close:
            tags.append("OR_BREAKOUT_DOWN")

        current_high = current.get("High")
        current_low = current.get("Low")
        previous_high = previous.get("High")
        previous_low = previous.get("Low")
        if all(
            isinstance(value, (int, float)) and not isinstance(value, bool)
            for value in (current_high, current_low, previous_high, previous_low)
        ) and current_high < previous_high and current_low > previous_low:
            tags.append("INSIDE_BAR")

    efficiency = current.get("MicroPathEfficiency")
    if isinstance(efficiency, (int, float)) and efficiency >= 0.7:
        tags.append("DIRECTIONAL_MICRO_PATH")
    return tags


__all__ = [
    "BrainDispatchDecision",
    "DispatchAction",
    "EffectivePlanDecision",
    "decide_brain_dispatch",
    "evaluate_anomaly_condition",
    "price_action_tags",
    "resolve_effective_plan",
]
