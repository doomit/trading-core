from __future__ import annotations

from .paper_execution import ExecutionConflict, PaperOrder, _aware


def cancel_pending(self, order: PaperOrder, intent, *, occurred_at):
    """Cancel one pending paper order without manufacturing a fill."""
    if order.status != "PENDING" or order.order_type not in {"LIMIT", "STOP"}:
        raise ValueError("order must be a pending LIMIT or STOP")
    if order.symbol != intent.symbol or order.side != intent.side or order.quantity != intent.quantity:
        raise ValueError("pending order and intent do not match")
    _aware(occurred_at, "occurred_at")

    filled = (
        self._filled_pending_limits.get(order.order_id)
        if order.order_type == "LIMIT"
        else getattr(self, "_filled_pending_stops", {}).get(order.order_id)
    )
    if filled is not None:
        raise ExecutionConflict("pending order is already filled")

    cancelled = PaperOrder(
        order_id=order.order_id,
        symbol=order.symbol,
        side=order.side,
        quantity=order.quantity,
        protective_stop_price=order.protective_stop_price,
        submitted_at=order.submitted_at,
        order_type=order.order_type,
        status="CANCELLED",
        limit_price=order.limit_price,
        stop_price=order.stop_price,
    )
    return cancelled, None
