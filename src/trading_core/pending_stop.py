from __future__ import annotations

import hashlib
from decimal import Decimal

from .paper_execution import PaperFill, PaperOrder, ROUND_TURN_COMMISSION_USD, _aware
from .paper_lifecycle import Bar


def process_pending_stop(self, order: PaperOrder, intent, *, bar: Bar, occurred_at):
    """Resolve one pending LONG or SHORT stop against a canonical closed OHLC bar."""
    if order.order_type != "STOP" or order.status != "PENDING" or order.stop_price is None:
        raise ValueError("order must be a pending STOP")
    if order.symbol != intent.symbol or order.side != intent.side or order.quantity != intent.quantity:
        raise ValueError("pending order and intent do not match")
    if order.side not in {"LONG", "SHORT"}:
        raise ValueError("pending STOP side is not supported")
    if not isinstance(bar, Bar):
        raise ValueError("bar must be a canonical Bar")
    _aware(occurred_at, "occurred_at")

    with self._pending_lock:
        filled_stops = getattr(self, "_filled_pending_stops", None)
        if filled_stops is None:
            filled_stops = {}
            self._filled_pending_stops = filled_stops
        existing = filled_stops.get(order.order_id)
        if existing is not None:
            return existing, None
        crossed = bar.high >= order.stop_price if order.side == "LONG" else bar.low <= order.stop_price
        if not crossed:
            return order, None

        fill_price = max(order.stop_price, bar.open) if order.side == "LONG" else min(order.stop_price, bar.open)
        slippage_points = fill_price - order.stop_price
        filled = PaperOrder(
            order_id=order.order_id,
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            protective_stop_price=order.protective_stop_price,
            submitted_at=order.submitted_at,
            order_type=order.order_type,
            status="FILLED",
            stop_price=order.stop_price,
        )
        identity = hashlib.sha256(f"{order.order_id}|{occurred_at.isoformat()}|STOP".encode()).hexdigest()
        commission_usd = ROUND_TURN_COMMISSION_USD * order.quantity / Decimal("2")
        fill = PaperFill(
            fill_id=f"paper-fill:{identity[:32]}",
            order_id=order.order_id,
            price=fill_price,
            quantity=order.quantity,
            occurred_at=occurred_at,
            reference_price=order.stop_price,
            slippage_points=slippage_points,
            commission_usd=commission_usd,
        )
        filled_stops[order.order_id] = filled
        return filled, fill
