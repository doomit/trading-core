from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent


NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def _intent() -> OrderIntent:
    return OrderIntent(
        event_id="evt_mes_pending_limit_1",
        plan_id="evt_mes_pending_limit_1",
        plan_hash="abc123",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        expected_fill_price=Decimal("6000.25"),
        protective_stop_price=Decimal("5990.00"),
        risk_usd=Decimal("53.75"),
        session_id="CME-2026-09-03",
        not_before=NOW,
    )


def _market(open_price: str = "6000.00") -> MarketSnapshot:
    return MarketSnapshot(
        symbol="MES1!",
        feed_as_of=NOW,
        next_bar_start=NOW,
        next_bar_open=Decimal(open_price),
        environment="PROD",
        data_class="REAL",
        source="tradingview",
        healthy=True,
        consecutive_closed_bars=3,
    )


def test_buy_limit_below_market_remains_pending_without_fabricated_fill():
    broker = DeterministicPaperBroker()

    order, fill = broker.submit_limit(
        _intent(),
        _market("6000.00"),
        limit_price=Decimal("5999.75"),
    )

    assert order.order_type == "LIMIT"
    assert order.status == "PENDING"
    assert order.limit_price == Decimal("5999.75")
    assert fill is None


def test_pending_buy_limit_fills_at_limit_when_closed_bar_trades_through():
    broker = DeterministicPaperBroker()
    intent = _intent()
    pending, entry_fill = broker.submit_limit(
        intent,
        _market("6000.00"),
        limit_price=Decimal("5999.75"),
    )
    assert entry_fill is None

    filled, fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.25"),
        occurred_at=NOW,
    )

    assert filled.order_id == pending.order_id
    assert filled.status == "FILLED"
    assert filled.order_type == "LIMIT"
    assert filled.limit_price == Decimal("5999.75")
    assert fill is not None
    assert fill.order_id == pending.order_id
    assert fill.price == Decimal("5999.75")
    assert fill.quantity == 1
    assert fill.occurred_at == NOW


def test_replayed_crossing_bar_does_not_emit_a_second_pending_limit_fill():
    broker = DeterministicPaperBroker()
    intent = _intent()
    pending, entry_fill = broker.submit_limit(
        intent,
        _market("6000.00"),
        limit_price=Decimal("5999.75"),
    )
    assert entry_fill is None

    filled, first_fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.25"),
        occurred_at=NOW,
    )
    replayed, duplicate_fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.25"),
        occurred_at=NOW,
    )

    assert first_fill is not None
    assert replayed == filled
    assert duplicate_fill is None


def test_buy_stop_above_market_remains_pending_without_fabricated_fill():
    broker = DeterministicPaperBroker()

    order, fill = broker.submit_stop(
        _intent(),
        _market("6000.00"),
        stop_price=Decimal("6000.25"),
    )

    assert order.order_type == "STOP"
    assert order.status == "PENDING"
    assert order.stop_price == Decimal("6000.25")
    assert fill is None


def test_pending_buy_stop_fills_at_stop_when_closed_bar_trades_through():
    broker = DeterministicPaperBroker()
    intent = _intent()
    pending, entry_fill = broker.submit_stop(
        intent,
        _market("6000.00"),
        stop_price=Decimal("6000.25"),
    )
    assert entry_fill is None

    filled, fill = broker.process_pending_stop(
        pending,
        intent,
        bar_low=Decimal("5999.75"),
        bar_high=Decimal("6000.50"),
        occurred_at=NOW,
    )

    assert filled.order_id == pending.order_id
    assert filled.status == "FILLED"
    assert filled.order_type == "STOP"
    assert filled.stop_price == Decimal("6000.25")
    assert fill is not None
    assert fill.order_id == pending.order_id
    assert fill.price == Decimal("6000.25")
    assert fill.quantity == 1
    assert fill.occurred_at == NOW
