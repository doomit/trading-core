from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any

from .simple_paper_position_execution import (
    apply_open_plan_update,
    execute_flat_plan as _execute_flat_plan,
    manage_open_position as _manage_open_position,
)


def execute_flat_plan(
    *,
    current_position: dict[str, Any],
    accepted_plan: dict[str, Any] | None,
    market: dict[str, Any] | None,
    executed_at: datetime,
    cycle_id: str,
    account: dict[str, Any],
    position_id: str,
    point_value: float,
    allow_new_risk: bool = True,
) -> dict[str, Any]:
    """Session-aware FLAT execution that never opens risk when the gate is closed."""
    if not allow_new_risk:
        return _no_action(current_position, account)
    return _execute_flat_plan(
        current_position=current_position,
        accepted_plan=accepted_plan,
        market=market,
        executed_at=executed_at,
        cycle_id=cycle_id,
        account=account,
        position_id=position_id,
        point_value=point_value,
    )


def manage_open_position(
    *,
    current_position: dict[str, Any],
    accepted_plan: dict[str, Any] | None,
    market: dict[str, Any] | None,
    executed_at: datetime,
    cycle_id: str,
    account: dict[str, Any],
    point_value: float,
    stale_after_minutes: int = 15,
    allow_new_risk: bool = True,
) -> dict[str, Any]:
    """Manage an OPEN position while suppressing only risk-increasing ADD behavior.

    When new risk is blocked, existing or newly proposed ADD instructions are removed.
    UPDATE protection and REDUCE instructions remain usable, and STOP/TP/EXIT behavior is
    delegated to the frozen position engine unchanged.
    """
    if allow_new_risk:
        return _manage_open_position(
            current_position=current_position,
            accepted_plan=accepted_plan,
            market=market,
            executed_at=executed_at,
            cycle_id=cycle_id,
            account=account,
            point_value=point_value,
            stale_after_minutes=stale_after_minutes,
        )

    position = deepcopy(current_position)
    plan_for_engine = accepted_plan
    state_changed = False

    if accepted_plan is not None and accepted_plan.get("decision") == "UPDATE":
        safe_plan = deepcopy(accepted_plan)
        safe_plan["add_once"] = None
        position = apply_open_plan_update(position, safe_plan, executed_at)
        plan_for_engine = None
        state_changed = True

    if position.get("pending_add") is not None:
        position["pending_add"] = None
        if not state_changed:
            position["position_version"] = int(position["position_version"]) + 1
            position["updated_at"] = _iso_z(executed_at)
            position["last_action_id"] = f"risk-gate:{_iso_z(executed_at)}"
        state_changed = True

    result = _manage_open_position(
        current_position=position,
        accepted_plan=plan_for_engine,
        market=market,
        executed_at=executed_at,
        cycle_id=cycle_id,
        account=account,
        point_value=point_value,
        stale_after_minutes=stale_after_minutes,
    )

    if state_changed and result.get("execution") is None and not result.get("changed"):
        result = dict(result)
        result["position"] = position
        result["changed"] = True
    return result


def _no_action(position: dict[str, Any], account: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": position,
        "account": account,
        "execution": None,
        "outcome": "NO_ACTION",
        "changed": False,
        "synthetic": False,
        "stale_feed": False,
    }


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return value.isoformat().replace("+00:00", "Z")


__all__ = ["execute_flat_plan", "manage_open_position"]
