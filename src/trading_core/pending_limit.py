from __future__ import annotations

import hashlib
from decimal import Decimal

from .paper_execution import ExecutionConflict, PaperFill, PaperOrder, ROUND_TURN_COMMISSION_USD, _aware, _decimal


def process_pending_limit(self, order: PaperOrder, intent, *, bar_low: Decimal, bar_high: Decimal, occurred_at):
    """Resolve one pending LONG or SHORT limit against a canonical closed OHLC range."""
    if order.order_type != "LIMIT" or order.status != "PENDING" or order.limit_price is None:
        raise ValueError("order must be a pending LIMIT")
    if order.symbol != intent.symbol or order.side != intent.side or order.quantity != intent.quantity:
        raise ValueError("pending order and intent do not match")
    if order.side not in {"LONG", "SHORT"}:
        raise ValueError("pending LIMIT side is not supported")
    _aware(occurred_at, "occurred_at")
    low = _decimal(bar_low, "bar_low")
    high = _decimal(bar_high, "bar_high")
    if low > high:
        raise ValueError("bar_low must not exceed bar_high")

    with self._pending_lock:
        cancelled_orders = getattr(self, "_cancelled_pending_orders", {})
        if order.order_id in cancelled_orders:
            raise ExecutionConflict("pending order is already cancelled")
        existing = self._filled_pending_limits.get(order.order_id)
        if existing is not None:
            return existing, None
        crossed = low <= order.limit_price if order.side == "LONG" else high >= order.limit_price
        if not crossed:
            return order, None
        filled = PaperOrder(
            order.order_id,
            order.symbol,
            order.side,
            order.quantity,
            order.protective_stop_price,
            order.submitted_at,
            order.order_type,
            "FILLED",
            order.limit_price,
        )
        identity = hashlib.sha256(f"{order.order_id}|{occurred_at.isoformat()}|LIMIT".encode()).hexdigest()
        commission_usd = ROUND_TURN_COMMISSION_USD * order.quantity / Decimal("2")
        fill = PaperFill(
            f"paper-fill:{identity[:32]}",
            order.order_id,
            order.limit_price,
            order.quantity,
            occurred_at,
            order.limit_price,
            Decimal("0"),
            commission_usd,
        )
        self._filled_pending_limits[order.order_id] = filled
        return filled, fill
