from __future__ import annotations

from datetime import datetime
from typing import Any


def fill_price_for_order(order: dict[str, Any], side: str, bar: dict[str, float]) -> float | None:
    """Resolve one explicit MARKET/LIMIT/STOP paper instruction against one bar."""
    order_type = order["order_type"]
    if order_type == "MARKET":
        return float(bar["close"])

    trigger = float(order["trigger_price"])
    if order_type == "LIMIT":
        touched = float(bar["low"]) <= trigger if side == "LONG" else float(bar["high"]) >= trigger
    elif order_type == "STOP":
        touched = float(bar["high"]) >= trigger if side == "LONG" else float(bar["low"]) <= trigger
    else:
        raise ValueError(f"unsupported order_type: {order_type!r}")
    return trigger if touched else None


def open_position_from_plan(
    *,
    current_position: dict[str, Any],
    plan: dict[str, Any],
    fill_price: float,
    executed_at: datetime,
    position_id: str,
) -> dict[str, Any]:
    """Create a new durable OPEN position from a matching OPEN plan."""
    if current_position.get("status") != "FLAT":
        raise ValueError("new position may open only from FLAT")
    if plan.get("decision") != "OPEN":
        raise ValueError("plan decision must be OPEN")
    if plan.get("symbol") != current_position.get("symbol"):
        raise ValueError("plan symbol must match current position symbol")

    entry = plan["entry"]
    protection = plan["protection"]
    executed_at_iso = executed_at.isoformat().replace("+00:00", "Z")

    return {
        "schema": "position_state_v1",
        "symbol": current_position["symbol"],
        "status": "OPEN",
        "position_id": position_id,
        "position_version": 0,
        "side": plan["side"],
        "qty": int(entry["qty"]),
        "avg_entry": float(fill_price),
        "stop_loss": float(protection["stop_loss"]),
        "take_profit": float(protection["take_profit"]),
        "active_plan_id": plan["plan_id"],
        "active_plan_generated_at": plan["generated_at"],
        "action_valid_until": plan["action_valid_until"],
        "pending_add": plan.get("add_once"),
        "pending_reduce": plan.get("reduce_once"),
        "opened_at": executed_at_iso,
        "updated_at": executed_at_iso,
        "closed_at": None,
        "last_action_id": f'{plan["plan_id"]}:open',
    }
