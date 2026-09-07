from __future__ import annotations

from datetime import datetime
from math import isclose
from typing import Any


ACCEPTED = "ACCEPTED"
ALREADY_OBSERVED = "ALREADY_OBSERVED"
IGNORED_POSITION_MISMATCH = "IGNORED_POSITION_MISMATCH"
IGNORED_EXPIRED = "IGNORED_EXPIRED"


def position_ref(position: dict[str, Any]) -> dict[str, Any]:
    """Return the exact position identity Brain plans must bind to."""
    status = position["status"]
    if status == "FLAT":
        return {"state": "FLAT", "position_id": None, "position_version": None}
    if status == "OPEN":
        return {
            "state": "OPEN",
            "position_id": position["position_id"],
            "position_version": position["position_version"],
        }
    raise ValueError(f"current position must be FLAT or OPEN, got {status!r}")


def candidate_plan_outcome(
    plan: dict[str, Any],
    current_position: dict[str, Any],
    observed_at: datetime,
    *,
    last_observed_plan_id: str | None = None,
) -> str:
    """Classify one candidate plan without mutating position or plan state."""
    if plan["plan_id"] == last_observed_plan_id:
        return ALREADY_OBSERVED

    if plan["target_position"] != position_ref(current_position):
        return IGNORED_POSITION_MISMATCH

    if observed_at > _parse_datetime(plan["action_valid_until"]):
        return IGNORED_EXPIRED

    return ACCEPTED


def plan_fits_quantity_limit(
    plan: dict[str, Any],
    current_position: dict[str, Any],
    max_contracts_per_symbol: int,
) -> bool:
    """Return whether the plan can ever increase exposure beyond the configured cap."""
    if max_contracts_per_symbol < 0:
        raise ValueError("max_contracts_per_symbol must be non-negative")

    if plan.get("decision") == "OPEN":
        entry = plan.get("entry") or {}
        base_qty = int(entry.get("qty", 0))
    else:
        base_qty = int(current_position.get("qty", 0))

    add_once = plan.get("add_once") or {}
    add_qty = int(add_once.get("qty", 0))
    return base_qty + add_qty <= max_contracts_per_symbol


def plan_latency_ms(
    analysis_bar_end: str,
    generated_at: str,
    pulled_at: datetime,
) -> tuple[int, int]:
    """Return (Brain generation latency, Azure plan pickup latency) in milliseconds."""
    analysis_time = _parse_datetime(analysis_bar_end)
    generation_time = _parse_datetime(generated_at)
    generation_ms = int((generation_time - analysis_time).total_seconds() * 1000)
    pickup_ms = int((pulled_at - generation_time).total_seconds() * 1000)
    if generation_ms < 0 or pickup_ms < 0:
        raise ValueError("plan timestamps must be monotonic")
    return generation_ms, pickup_ms


def validate_paper_account_state_semantics(account: dict[str, Any]) -> None:
    """Validate arithmetic invariants that JSON Schema cannot express."""
    expected_balance = float(account["starting_balance_usd"]) + float(account["realized_pnl_usd"])
    actual_balance = float(account["balance_usd"])
    if not isclose(actual_balance, expected_balance, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("balance_usd must equal starting_balance_usd + realized_pnl_usd")


def validate_paper_execution_semantics(execution: dict[str, Any]) -> None:
    """Validate deterministic position-version transitions for one paper execution."""
    action = execution["action"]
    before = execution["position_version_before"]
    after = execution["position_version_after"]

    if action == "OPEN":
        if before is not None or after != 0:
            raise ValueError("OPEN must create position version 0 from no previous position version")
        if not isclose(float(execution["realized_pnl_usd"]), 0.0, rel_tol=0.0, abs_tol=1e-6):
            raise ValueError("OPEN must not realize P&L")
    else:
        if not isinstance(before, int) or after != before + 1:
            raise ValueError("position_version_after must equal position_version_before + 1")

    if action == "ADD" and not isclose(
        float(execution["realized_pnl_usd"]), 0.0, rel_tol=0.0, abs_tol=1e-6
    ):
        raise ValueError("ADD must not realize P&L")

    if action == "STALE_FEED_FORCED_STOP" and execution["synthetic"] is not True:
        raise ValueError("STALE_FEED_FORCED_STOP must be synthetic")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed
