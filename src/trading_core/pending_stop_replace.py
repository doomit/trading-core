from __future__ import annotations

from .paper_execution import PaperOrder


def replace_pending_stop(self, order: PaperOrder, intent, market_state, *, new_stop_price, occurred_at):
    """Cancel one pending STOP and create a deterministic replacement identity."""
    if order.order_type != "STOP" or order.status != "PENDING":
        raise ValueError("order must be a pending STOP")
    cancelled, cancel_fill = self.cancel_pending(order, intent, occurred_at=occurred_at)
    if cancel_fill is not None:
        raise RuntimeError("pending cancellation must not emit a fill")
    replacement, replacement_fill = self.submit_stop(intent, market_state, stop_price=new_stop_price)
    if replacement.order_id == order.order_id:
        raise ValueError("replacement stop must change order identity")
    return cancelled, replacement, replacement_fill
