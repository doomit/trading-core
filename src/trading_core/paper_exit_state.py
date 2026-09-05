from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import Any, Iterable

from .paper_bracket import PaperBracketRelationship, apply_oco_fill
from .paper_execution import ExecutionResult
from .paper_exit import close_open_position
from .paper_lifecycle import Bar


def _target_consumed_receipt_present(result: ExecutionResult) -> bool:
    return any(
        isinstance(receipt, dict)
        and receipt.get("stage") == "PAPER_EXIT_FILLED"
        and receipt.get("reason_code") in {"TARGET_PARTIAL_FILLED", "TARGET_FILLED"}
        for receipt in result.receipts
    )


def _validate_relationship(
    plan: dict[str, Any],
    result: ExecutionResult,
    relationship: PaperBracketRelationship,
) -> None:
    if not isinstance(relationship, PaperBracketRelationship):
        raise TypeError("relationship must be PaperBracketRelationship")
    if result.terminal or result.position is None or result.position.status != "OPEN":
        raise ValueError("result must contain one OPEN nonterminal position")
    if relationship.status != "ACTIVE":
        raise ValueError("relationship must be ACTIVE while position is OPEN")
    if relationship.parent_order_id != result.position.order_id:
        raise ValueError("relationship parent_order_id does not match open position")
    if relationship.remaining_quantity != result.position.quantity:
        raise ValueError("relationship remaining_quantity does not match open position")

    action = plan.get("position_action")
    if not isinstance(action, dict):
        raise ValueError("position_action must be an object")
    original_quantity = action.get("quantity")
    if isinstance(original_quantity, bool) or not isinstance(original_quantity, int) or original_quantity < 1:
        raise ValueError("position_action.quantity must be a positive integer")
    if relationship.original_quantity != original_quantity:
        raise ValueError("relationship original_quantity does not match immutable plan")
    configured_target_quantity = action.get("target_exit_quantity", original_quantity)
    if configured_target_quantity is None:
        configured_target_quantity = original_quantity
    if relationship.target_quantity != configured_target_quantity:
        raise ValueError("relationship target_quantity does not match immutable plan")

    receipt_consumed = _target_consumed_receipt_present(result)
    relationship_consumed = relationship.active_target_quantity == 0
    if receipt_consumed and not relationship_consumed:
        raise ValueError("execution receipts show target consumed but durable relationship is still active")


def _project_relationship_state_into_legacy_result(
    result: ExecutionResult,
    relationship: PaperBracketRelationship,
) -> tuple[ExecutionResult, bool]:
    """Project durable target-consumed state into the legacy resolver without persisting fake evidence."""
    if relationship.active_target_quantity != 0 or _target_consumed_receipt_present(result):
        return result, False
    marker = {
        "stage": "PAPER_EXIT_FILLED",
        "reason_code": "TARGET_PARTIAL_FILLED",
        "_paper_bracket_state_projection": True,
    }
    return replace(result, receipts=result.receipts + (marker,)), True


def _strip_projection(result: ExecutionResult) -> ExecutionResult:
    receipts = tuple(
        receipt
        for receipt in result.receipts
        if not (isinstance(receipt, dict) and receipt.get("_paper_bracket_state_projection") is True)
    )
    if receipts == result.receipts:
        return result
    return replace(result, receipts=receipts)


def close_open_position_with_bracket(
    plan: dict[str, Any],
    entry_result: ExecutionResult,
    relationship: PaperBracketRelationship,
    bar: Bar,
    *,
    occurred_at: datetime,
) -> tuple[ExecutionResult, PaperBracketRelationship]:
    """Advance one OPEN result and its authoritative durable OCO relationship together.

    The relationship is validated against immutable plan/position identity before
    resolution. If durable state says a one-shot target has already been consumed,
    that state drives the legacy bar resolver even if execution receipts were lost
    during adapter restart. The projection marker is removed before returning, so
    no synthetic audit receipt is persisted.
    """
    _validate_relationship(plan, entry_result, relationship)
    projected, _ = _project_relationship_state_into_legacy_result(entry_result, relationship)
    advanced = close_open_position(plan, projected, bar, occurred_at=occurred_at)
    advanced = _strip_projection(advanced)

    if advanced is entry_result or (
        advanced.position is not None
        and entry_result.position is not None
        and advanced.position.quantity == entry_result.position.quantity
        and advanced.trade is entry_result.trade
    ):
        return advanced, relationship
    if advanced.order is None or advanced.fill is None:
        raise ValueError("paper exit must expose child order and fill")
    if advanced.order.order_id not in {relationship.stop_order_id, relationship.target_order_id}:
        raise ValueError("paper exit child order does not belong to durable relationship")

    updated = apply_oco_fill(
        relationship,
        filled_order_id=advanced.order.order_id,
        filled_quantity=advanced.fill.quantity,
    )
    if advanced.position is None or updated.remaining_quantity != advanced.position.quantity:
        raise ValueError("advanced position quantity diverges from durable relationship")
    if advanced.terminal != (updated.status == "CLOSED"):
        raise ValueError("execution terminal state diverges from durable relationship")
    return advanced, updated


def advance_open_position_through_bars_with_bracket(
    plan: dict[str, Any],
    entry_result: ExecutionResult,
    relationship: PaperBracketRelationship,
    bars: Iterable[tuple[Bar, datetime]],
) -> tuple[ExecutionResult, PaperBracketRelationship]:
    """Advance ordered closed bars while carrying authoritative bracket state."""
    result = entry_result
    current_relationship = relationship
    for bar, occurred_at in bars:
        if result.terminal:
            break
        result, current_relationship = close_open_position_with_bracket(
            plan,
            result,
            current_relationship,
            bar,
            occurred_at=occurred_at,
        )
    return result, current_relationship


__all__ = [
    "advance_open_position_through_bars_with_bracket",
    "close_open_position_with_bracket",
]
