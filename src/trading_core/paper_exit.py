from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import datetime
from decimal import Decimal
from typing import Any

from .paper_execution import (
    ROUND_TURN_COMMISSION_USD,
    ExecutionResult,
    PaperFill,
    PaperOrder,
    PaperPositionRecord,
    PaperTrade,
    _aware,
    _decimal,
    _receipt,
    canonical_plan_hash,
)
from .paper_lifecycle import Bar, PaperPosition, resolve_bracket_bar


def close_open_position(
    plan: dict[str, Any],
    entry_result: ExecutionResult,
    bar: Bar,
    *,
    occurred_at: datetime,
) -> ExecutionResult:
    """Advance one OPEN paper position through a closed-bar OCO resolution.

    The immutable plan remains the source of the protective stop, target, and
    optional one-shot target exit quantity. A bar that touches neither active
    leg returns the unchanged nonterminal result. Partial target fills emit a
    durable EXIT record while carrying the remaining OPEN quantity forward;
    the prior partial receipt makes that target inactive on later bars. The
    first full close emits the terminal COMPLETED receipt.
    """
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    _aware(occurred_at, "occurred_at")
    if not isinstance(bar, Bar):
        raise ValueError("bar must be a paper_lifecycle.Bar")

    plan_hash = canonical_plan_hash(plan)
    event_id = plan.get("trigger_event_id")
    plan_id = plan.get("plan_id")
    if (
        not isinstance(event_id, str)
        or not event_id
        or not isinstance(plan_id, str)
        or not plan_id
        or plan_id != event_id
        or entry_result.event_id != event_id
        or entry_result.plan_id != plan_id
        or entry_result.plan_hash != plan_hash
    ):
        raise ValueError("entry result does not match immutable plan identity")
    if entry_result.terminal or entry_result.position is None or entry_result.position.status != "OPEN":
        raise ValueError("entry result must contain one OPEN nonterminal position")
    if occurred_at < entry_result.position.opened_at:
        raise ValueError("exit cannot occur before position open")

    action = plan.get("position_action")
    if not isinstance(action, dict):
        raise ValueError("position_action must be an object")
    stop = action.get("protective_stop")
    target = action.get("take_profit")
    if not isinstance(stop, dict) or "price" not in stop:
        raise ValueError("protective_stop.price is required")
    if not isinstance(target, dict) or "price" not in target:
        raise ValueError("take_profit.price is required for bracket lifecycle")

    position_record = entry_result.position
    target_consumed = any(
        isinstance(receipt, dict)
        and receipt.get("stage") == "PAPER_EXIT_FILLED"
        and receipt.get("reason_code") == "TARGET_PARTIAL_FILLED"
        for receipt in entry_result.receipts
    )
    target_exit_quantity = action.get("target_exit_quantity")
    if target_exit_quantity is not None and not target_consumed:
        if (
            isinstance(target_exit_quantity, bool)
            or not isinstance(target_exit_quantity, int)
            or target_exit_quantity < 1
            or target_exit_quantity > position_record.quantity
        ):
            raise ValueError("target_exit_quantity must be between 1 and open position quantity")
    else:
        target_exit_quantity = None

    lifecycle_position = PaperPosition(
        position_id=position_record.position_id,
        symbol=position_record.symbol,
        side=position_record.side,
        quantity=position_record.quantity,
        entry_price=position_record.entry_price,
        stop_price=_decimal(stop["price"], "protective_stop.price"),
        target_price=_decimal(target["price"], "take_profit.price"),
        target_exit_quantity=target_exit_quantity,
        target_consumed=target_consumed,
    )
    resolution = resolve_bracket_bar(lifecycle_position, bar)
    if resolution.exit_quantity == 0:
        return entry_result
    if resolution.exit_price is None or resolution.exit_quantity < 1:
        raise ValueError("closed bracket resolution must contain an exit fill")

    identity = hashlib.sha256(
        f"{event_id}|{plan_id}|{plan_hash}|{position_record.position_id}|{resolution.reason_code}|{resolution.exit_price}".encode("utf-8")
    ).hexdigest()
    exit_order_id = f"paper-exit-order:{identity[:32]}"
    exit_fill_id = f"paper-exit-fill:{identity[:32]}"
    exit_side = "SHORT" if position_record.side == "LONG" else "LONG"
    exit_order = PaperOrder(
        exit_order_id,
        position_record.symbol,
        exit_side,
        resolution.exit_quantity,
        lifecycle_position.stop_price,
        occurred_at,
    )
    reference_price = (
        lifecycle_position.target_price
        if resolution.reason_code in {"TARGET_FILLED", "TARGET_PARTIAL_FILLED"}
        else lifecycle_position.stop_price
    )
    slippage_points = resolution.exit_price - reference_price
    commission_usd = ROUND_TURN_COMMISSION_USD * resolution.exit_quantity / Decimal("2")
    exit_fill = PaperFill(
        exit_fill_id,
        exit_order_id,
        resolution.exit_price,
        resolution.exit_quantity,
        occurred_at,
        reference_price,
        slippage_points,
        commission_usd,
    )
    updated_position = PaperPositionRecord(
        position_record.position_id,
        event_id,
        plan_id,
        position_record.order_id,
        position_record.entry_fill_id,
        position_record.symbol,
        position_record.side,
        resolution.remaining_quantity,
        position_record.entry_price,
        position_record.opened_at,
        "OPEN" if resolution.remaining_quantity else "CLOSED",
    )
    exit_trade = PaperTrade(
        f"paper-exit-trade:{identity[:32]}",
        event_id,
        plan_id,
        exit_order_id,
        exit_fill_id,
        position_record.position_id,
        position_record.symbol,
        exit_side,
        resolution.exit_quantity,
        resolution.exit_price,
        occurred_at,
        "EXIT",
        exit_fill.slippage_points,
        exit_fill.commission_usd,
    )
    exit_receipt = _receipt(
        event_id=event_id,
        plan_id=plan_id,
        stage="PAPER_EXIT_FILLED",
        status="PASS",
        source="paper_lifecycle",
        occurred_at=occurred_at,
        reason_code=resolution.reason_code,
        decision=plan.get("decision") if isinstance(plan.get("decision"), str) else None,
    )
    exit_receipt["receipt_id"] = f"paper:{identity[:32]}"
    if resolution.remaining_quantity:
        return ExecutionResult(
            event_id,
            plan_id,
            plan_hash,
            "OPEN",
            resolution.reason_code,
            False,
            entry_result.receipts + (exit_receipt,),
            exit_order,
            exit_fill,
            updated_position,
            exit_trade,
        )

    completed = _receipt(
        event_id=event_id,
        plan_id=plan_id,
        stage="COMPLETED",
        status="PASS",
        source="azure_executor",
        occurred_at=occurred_at,
        reason_code=resolution.reason_code,
        decision=plan.get("decision") if isinstance(plan.get("decision"), str) else None,
    )
    return ExecutionResult(
        event_id,
        plan_id,
        plan_hash,
        "CLOSED",
        resolution.reason_code,
        True,
        entry_result.receipts + (exit_receipt, completed),
        exit_order,
        exit_fill,
        updated_position,
        exit_trade,
    )


def advance_open_position_through_bars(
    plan: dict[str, Any],
    entry_result: ExecutionResult,
    bars: Iterable[tuple[Bar, datetime]],
) -> ExecutionResult:
    """Advance an OPEN paper result through ordered closed bars statefully.

    Each bar receives the result of the preceding bar so partial exits,
    remaining quantity, and one-shot target consumption cannot be lost by an
    adapter that replays every bar against the original entry snapshot.
    Processing stops at the first terminal result.
    """
    result = entry_result
    for bar, occurred_at in bars:
        if result.terminal:
            break
        result = close_open_position(
            plan,
            result,
            bar,
            occurred_at=occurred_at,
        )
    return result


__all__ = ["advance_open_position_through_bars", "close_open_position"]
