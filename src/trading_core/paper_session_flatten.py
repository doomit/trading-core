from __future__ import annotations

import hashlib
from datetime import datetime
from decimal import Decimal

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
)


def flatten_open_position_at_session_close(
    entry_result: ExecutionResult,
    *,
    market_price: Decimal,
    occurred_at: datetime,
) -> ExecutionResult:
    """Close one OPEN paper position at the observed session-close market price."""
    _aware(occurred_at, "occurred_at")
    price = _decimal(market_price, "market_price")
    if price <= 0:
        raise ValueError("market_price must be positive")
    position = entry_result.position
    if entry_result.terminal or position is None or position.status != "OPEN" or position.quantity < 1:
        raise ValueError("entry result must contain one OPEN nonterminal position")
    if occurred_at < position.opened_at:
        raise ValueError("session flatten cannot occur before position open")

    identity = hashlib.sha256(
        f"{entry_result.event_id}|{entry_result.plan_id}|{entry_result.plan_hash}|{position.position_id}|SESSION_FLATTENED|{price}".encode("utf-8")
    ).hexdigest()
    order_id = f"paper-session-flatten-order:{identity[:32]}"
    fill_id = f"paper-session-flatten-fill:{identity[:32]}"
    exit_side = "SHORT" if position.side == "LONG" else "LONG"
    order = PaperOrder(
        order_id,
        position.symbol,
        exit_side,
        position.quantity,
        position.entry_price,
        occurred_at,
    )
    commission_usd = ROUND_TURN_COMMISSION_USD * position.quantity / Decimal("2")
    fill = PaperFill(
        fill_id,
        order_id,
        price,
        position.quantity,
        occurred_at,
        price,
        Decimal("0"),
        commission_usd,
    )
    closed_position = PaperPositionRecord(
        position.position_id,
        position.event_id,
        position.plan_id,
        position.order_id,
        position.entry_fill_id,
        position.symbol,
        position.side,
        0,
        position.entry_price,
        position.opened_at,
        "CLOSED",
    )
    trade = PaperTrade(
        f"paper-session-flatten-trade:{identity[:32]}",
        entry_result.event_id,
        entry_result.plan_id,
        order_id,
        fill_id,
        position.position_id,
        position.symbol,
        exit_side,
        position.quantity,
        price,
        occurred_at,
        "EXIT",
        Decimal("0"),
        commission_usd,
    )
    exit_receipt = _receipt(
        event_id=entry_result.event_id,
        plan_id=entry_result.plan_id,
        stage="PAPER_EXIT_FILLED",
        status="PASS",
        source="paper_lifecycle",
        occurred_at=occurred_at,
        reason_code="SESSION_FLATTENED",
    )
    completed = _receipt(
        event_id=entry_result.event_id,
        plan_id=entry_result.plan_id,
        stage="COMPLETED",
        status="PASS",
        source="azure_executor",
        occurred_at=occurred_at,
        reason_code="SESSION_FLATTENED",
    )
    return ExecutionResult(
        entry_result.event_id,
        entry_result.plan_id,
        entry_result.plan_hash,
        "CLOSED",
        "SESSION_FLATTENED",
        True,
        entry_result.receipts + (exit_receipt, completed),
        order,
        fill,
        closed_position,
        trade,
        entry_result.exit_trades + (trade,),
    )
