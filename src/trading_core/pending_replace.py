from __future__ import annotations

from .paper_execution import PaperOrder


def replace_pending_limit(self, order: PaperOrder, intent, market_state, *, new_limit_price, occurred_at):
    """Cancel one pending LIMIT and create a deterministic replacement identity."""
    if order.order_type != "LIMIT" or order.status != "PENDING":
        raise ValueError("order must be a pending LIMIT")
    cancelled, cancel_fill = self.cancel_pending(order, intent, occurred_at=occurred_at)
    if cancel_fill is not None:
        raise RuntimeError("pending cancellation must not emit a fill")
    replacement, replacement_fill = self.submit_limit(intent, market_state, limit_price=new_limit_price)
    if replacement.order_id == order.order_id:
        raise ValueError("replacement limit must change order identity")
    return cancelled, replacement, replacement_fill
