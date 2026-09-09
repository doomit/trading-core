from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from datetime import datetime
from math import isclose
from typing import Any

from .simple_paper_contracts import validate_paper_account_id


_EXECUTION_EVENT_TYPES = {
    "OPEN": "POSITION_OPENED",
    "ADD": "POSITION_ADDED",
    "REDUCE": "POSITION_REDUCED",
    "EXIT": "POSITION_EXITED",
    "STOP": "POSITION_STOPPED",
    "TAKE_PROFIT": "POSITION_TARGET_HIT",
    "EOD_FORCED_CLOSE": "POSITION_EOD_CLOSED",
    "STALE_FEED_FORCED_STOP": "POSITION_STALE_FEED_CLOSED",
}
_TERMINAL_EVENT_TYPES = {
    "POSITION_EXITED",
    "POSITION_STOPPED",
    "POSITION_TARGET_HIT",
    "POSITION_EOD_CLOSED",
    "POSITION_STALE_FEED_CLOSED",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _semantic_position(position: dict[str, Any]) -> dict[str, Any]:
    value = deepcopy(position)
    for key in ("updated_at", "event_sequence", "last_event_id"):
        value.pop(key, None)
    return value


def _event_type(position_before: dict[str, Any], position_after: dict[str, Any], execution: dict[str, Any] | None) -> str:
    if execution is not None:
        action = execution.get("action")
        if action not in _EXECUTION_EVENT_TYPES:
            raise ValueError(f"unsupported semantic execution action: {action!r}")
        return _EXECUTION_EVENT_TYPES[action]

    before_pending = (position_before.get("pending_add"), position_before.get("pending_reduce"))
    after_pending = (position_after.get("pending_add"), position_after.get("pending_reduce"))
    if before_pending != after_pending:
        if any(value is not None for value in before_pending) and all(value is None for value in after_pending):
            if str(position_after.get("last_action_id") or "").startswith("expire:"):
                return "PENDING_ORDERS_EXPIRED"
        return "PENDING_ORDERS_UPDATED"
    if (
        position_before.get("stop_loss") != position_after.get("stop_loss")
        or position_before.get("take_profit") != position_after.get("take_profit")
    ):
        return "PROTECTION_UPDATED"
    raise ValueError("changed transition has no recognized semantic position event")


def derive_position_event(
    *,
    account_before: dict[str, Any],
    position_before: dict[str, Any],
    transition: dict[str, Any],
) -> dict[str, Any] | None:
    """Derive one deterministic immutable event for one semantic position transition."""
    position_after = deepcopy(transition["position"])
    account_after = deepcopy(transition["account"])
    execution = deepcopy(transition.get("execution"))

    account_id = validate_paper_account_id(account_after["account_id"])
    if account_before.get("account_id") != account_id:
        raise ValueError("account_before and account_after account_id must match")
    if position_before.get("account_id") != account_id or position_after.get("account_id") != account_id:
        raise ValueError("position account_id must match transition account_id")

    if execution is None and _semantic_position(position_before) == _semantic_position(position_after):
        return None

    event_type = _event_type(position_before, position_after, execution)
    sequence = int(position_before.get("event_sequence", 0)) + 1
    position_id = position_after.get("position_id") or position_before.get("position_id")
    if not position_id:
        raise ValueError("semantic position event requires a position_id")

    position_after["event_sequence"] = sequence
    position_after["last_event_id"] = None
    execution_id = None if execution is None else execution["execution_id"]
    plan_id = (
        execution.get("plan_id") if execution is not None else position_after.get("active_plan_id")
    )
    occurred_at = execution["executed_at"] if execution is not None else position_after["updated_at"]

    identity = {
        "account_id": account_id,
        "position_id": position_id,
        "sequence": sequence,
        "event_type": event_type,
        "execution_id": execution_id,
        "plan_id": plan_id,
        "position_after": _semantic_position(position_after),
    }
    event_id = "pevt-" + hashlib.sha256(_canonical(identity).encode("utf-8")).hexdigest()[:32]
    position_after["last_event_id"] = event_id

    qty_delta = int(position_after.get("qty", 0)) - int(position_before.get("qty", 0))
    event = {
        "schema": "position_event_v1",
        "event_id": event_id,
        "account_id": account_id,
        "account_type": account_after["account_type"],
        "broker": account_after["broker"],
        "environment": account_after["environment"],
        "symbol": position_after["symbol"],
        "position_id": position_id,
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "plan_id": plan_id,
        "execution_id": execution_id,
        "position_before": deepcopy(position_before),
        "position_after": position_after,
        "account_before": deepcopy(account_before),
        "account_after": account_after,
        "execution": execution,
        "qty_delta": qty_delta,
        "fill_price": None if execution is None else float(execution["price"]),
        "realized_pnl_delta_usd": 0.0 if execution is None else float(execution["realized_pnl_usd"]),
        "reason": event_type if execution is None else execution["reason"],
    }
    validate_position_event_semantics(event)
    return event


def validate_position_event_semantics(event: dict[str, Any]) -> None:
    """Validate invariants that make retry/reconciliation safe."""
    account_id = validate_paper_account_id(event["account_id"])
    if int(event["sequence"]) < 1:
        raise ValueError("position event sequence must start at 1")
    after = event["position_after"]
    before = event["position_before"]
    if after.get("account_id") != account_id or before.get("account_id") != account_id:
        raise ValueError("position event account identity mismatch")
    if event["account_before"].get("account_id") != account_id or event["account_after"].get("account_id") != account_id:
        raise ValueError("position event account projection identity mismatch")
    if int(after.get("event_sequence", -1)) != int(event["sequence"]):
        raise ValueError("position_after event_sequence must equal event sequence")
    if after.get("last_event_id") != event["event_id"]:
        raise ValueError("position_after last_event_id must equal event_id")
    if int(before.get("event_sequence", 0)) + 1 != int(event["sequence"]):
        raise ValueError("position event sequence must advance exactly once")
    execution = event.get("execution")
    expected_pnl = 0.0
    if execution is not None:
        if execution.get("execution_id") != event.get("execution_id"):
            raise ValueError("execution_id must match embedded execution")
        if execution.get("account_id") != account_id:
            raise ValueError("embedded execution account_id mismatch")
        expected_pnl = float(execution["realized_pnl_usd"])
    elif event.get("execution_id") is not None:
        raise ValueError("execution_id requires embedded execution")
    if not isclose(float(event["realized_pnl_delta_usd"]), expected_pnl, rel_tol=0.0, abs_tol=1e-6):
        raise ValueError("realized_pnl_delta_usd must match execution")


def _fill(execution: dict[str, Any]) -> dict[str, Any]:
    return {
        "execution_id": execution["execution_id"],
        "action": execution["action"],
        "side": execution["side"],
        "qty": int(execution["qty"]),
        "price": float(execution["price"]),
        "executed_at": execution["executed_at"],
        "realized_pnl_usd": float(execution["realized_pnl_usd"]),
    }


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def project_trade_ledger(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Project one position's exact PAPER trade ledger from immutable semantic events."""
    if not events:
        raise ValueError("trade ledger requires at least one event")
    ordered = sorted((deepcopy(event) for event in events), key=lambda event: int(event["sequence"]))
    first = ordered[0]
    account_id = validate_paper_account_id(first["account_id"])
    symbol = first["symbol"]
    position_id = first["position_id"]
    expected_sequences = list(range(1, len(ordered) + 1))
    if [int(event["sequence"]) for event in ordered] != expected_sequences:
        raise ValueError("trade event sequence must be contiguous from 1")
    for event in ordered:
        if event["account_id"] != account_id or event["symbol"] != symbol or event["position_id"] != position_id:
            raise ValueError("trade ledger events must belong to one account/symbol/position")
    if first["event_type"] != "POSITION_OPENED":
        raise ValueError("trade ledger must start with POSITION_OPENED")

    entry_fills: list[dict[str, Any]] = []
    add_fills: list[dict[str, Any]] = []
    reduce_fills: list[dict[str, Any]] = []
    exit_fill: dict[str, Any] | None = None
    exit_reason: str | None = None
    execution_ids: list[str] = []
    gross = 0.0
    closed_at: str | None = None

    for event in ordered:
        gross += float(event["realized_pnl_delta_usd"])
        execution = event.get("execution")
        if execution is None:
            continue
        execution_ids.append(execution["execution_id"])
        action = execution["action"]
        if action == "OPEN":
            entry_fills.append(_fill(execution))
        elif action == "ADD":
            add_fills.append(_fill(execution))
        elif action == "REDUCE":
            reduce_fills.append(_fill(execution))
        elif event["event_type"] in _TERMINAL_EVENT_TYPES:
            exit_fill = _fill(execution)
            exit_reason = event["reason"]
            closed_at = event["occurred_at"]

    opened_at = first["occurred_at"]
    duration_ms = None
    if closed_at is not None:
        duration_ms = int((_parse_ts(closed_at) - _parse_ts(opened_at)).total_seconds() * 1000)
    side = first["position_after"].get("side")
    if side not in {"LONG", "SHORT"}:
        raise ValueError("opening event must preserve LONG/SHORT side")

    fees = 0.0
    slippage = 0.0
    return {
        "schema": "trade_ledger_v1",
        "account_id": account_id,
        "symbol": symbol,
        "position_id": position_id,
        "opening_plan_id": first.get("plan_id"),
        "side": side,
        "opened_at": opened_at,
        "closed_at": closed_at,
        "entry_fills": entry_fills,
        "add_fills": add_fills,
        "reduce_fills": reduce_fills,
        "exit_fill": exit_fill,
        "exit_reason": exit_reason,
        "gross_realized_pnl_usd": gross,
        "fees_usd": fees,
        "slippage_usd": slippage,
        "net_realized_pnl_usd": gross - fees - slippage,
        "duration_ms": duration_ms,
        "event_ids": [event["event_id"] for event in ordered],
        "execution_ids": execution_ids,
    }


__all__ = [
    "derive_position_event",
    "project_trade_ledger",
    "validate_position_event_semantics",
]
