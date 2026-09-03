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
