from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import ExecutionResult, PaperFill, PaperOrder, PaperPositionRecord, PaperTrade
from trading_core.paper_exit import flatten_open_position_at_session_close


OPENED = datetime(2026, 9, 3, 19, 0, tzinfo=timezone.utc)
CLOSED = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)


def _open_entry() -> ExecutionResult:
    order = PaperOrder("paper-order:entry", "MES1!", "LONG", 1, Decimal("5990.00"), OPENED)
    fill = PaperFill("paper-fill:entry", order.order_id, Decimal("6000.25"), 1, OPENED)
    position = PaperPositionRecord(
        "paper-position:entry",
        "evt_session_flatten",
        "evt_session_flatten",
        order.order_id,
        fill.fill_id,
        "MES1!",
        "LONG",
        1,
        fill.price,
        OPENED,
        "OPEN",
    )
    trade = PaperTrade(
        "paper-trade:entry",
        "evt_session_flatten",
        "evt_session_flatten",
        order.order_id,
        fill.fill_id,
        position.position_id,
        "MES1!",
        "LONG",
        1,
        fill.price,
        OPENED,
        "ENTRY",
    )
    return ExecutionResult(
        "evt_session_flatten",
        "evt_session_flatten",
        "plan-hash-session-flatten",
        "OPEN",
        "PAPER_FILLED",
        False,
        (),
        order,
        fill,
        position,
        trade,
    )


def test_session_flatten_closes_open_position_at_observed_market_price():
    result = flatten_open_position_at_session_close(
        _open_entry(),
        market_price=Decimal("5998.75"),
        occurred_at=CLOSED,
    )

    assert result.status == "CLOSED"
    assert result.terminal is True
    assert result.reason_code == "SESSION_FLATTENED"
    assert result.position is not None and result.position.status == "CLOSED"
    assert result.position.quantity == 0
    assert result.order is not None and result.order.side == "SHORT"
    assert result.order.quantity == 1
    assert result.fill is not None and result.fill.price == Decimal("5998.75")
    assert result.fill.reference_price == Decimal("5998.75")
    assert result.fill.slippage_points == Decimal("0")
    assert result.trade is not None and result.trade.role == "EXIT"
    assert result.trade.position_id == "paper-position:entry"
    assert result.receipts[-2]["stage"] == "PAPER_EXIT_FILLED"
    assert result.receipts[-2]["reason_code"] == "SESSION_FLATTENED"
    assert result.receipts[-1]["stage"] == "COMPLETED"
