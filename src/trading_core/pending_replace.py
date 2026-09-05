from __future__ import annotations

import hashlib

from .paper_execution import PaperOrder


def _replacement_order_id(previous_order_id: str, submitted_order_id: str) -> str:
    identity = hashlib.sha256(
        f"{previous_order_id}|REPLACE|{submitted_order_id}".encode()
    ).hexdigest()
    return f"paper-order:{identity[:32]}"


def replace_pending_limit(self, order: PaperOrder, intent, market_state, *, new_limit_price, occurred_at):
    """Cancel one pending LIMIT and create a deterministic replacement identity."""
    if order.order_type != "LIMIT" or order.status != "PENDING":
        raise ValueError("order must be a pending LIMIT")
    cancelled, cancel_fill = self.cancel_pending(order, intent, occurred_at=occurred_at)
    if cancel_fill is not None:
        raise RuntimeError("pending cancellation must not emit a fill")
    submitted, replacement_fill = self.submit_limit(intent, market_state, limit_price=new_limit_price)
    replacement = PaperOrder(
        order_id=_replacement_order_id(order.order_id, submitted.order_id),
        symbol=submitted.symbol,
        side=submitted.side,
        quantity=submitted.quantity,
        protective_stop_price=submitted.protective_stop_price,
        submitted_at=submitted.submitted_at,
        order_type=submitted.order_type,
        status=submitted.status,
        limit_price=submitted.limit_price,
        stop_price=submitted.stop_price,
    )
    if replacement.order_id == order.order_id:
        raise ValueError("replacement limit must change order identity")
    return cancelled, replacement, replacement_fill
