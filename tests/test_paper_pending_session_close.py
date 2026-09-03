from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent


NOW = datetime(2026, 9, 3, 20, 0, tzinfo=timezone.utc)


def _intent() -> OrderIntent:
    return OrderIntent(
        event_id="evt_mes_session_close",
        plan_id="evt_mes_session_close",
        plan_hash="session-close",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        expected_fill_price=Decimal("6000.25"),
        protective_stop_price=Decimal("5990.00"),
        risk_usd=Decimal("53.75"),
        session_id="CME-2026-09-03",
        not_before=NOW,
    )


def _market() -> MarketSnapshot:
    return MarketSnapshot(
        symbol="MES1!",
        feed_as_of=NOW,
        next_bar_start=NOW,
        next_bar_open=Decimal("6000.00"),
        environment="PROD",
        data_class="REAL",
        source="tradingview",
        healthy=True,
        consecutive_closed_bars=3,
    )


def test_session_close_cancels_pending_limit_without_fabricated_fill():
    intent = _intent()
    broker = DeterministicPaperBroker()
    pending, entry_fill = broker.submit_limit(intent, _market(), limit_price=Decimal("5999.75"))
    assert entry_fill is None

    cancelled, cancel_fill = broker.cancel_pending_for_session_close(pending, intent, occurred_at=NOW)

    assert cancelled.order_id == pending.order_id
    assert cancelled.status == "CANCELLED"
    assert cancelled.order_type == "LIMIT"
    assert cancelled.limit_price == pending.limit_price
    assert cancel_fill is None


def test_session_close_cancels_pending_stop_without_fabricated_fill():
    intent = _intent()
    broker = DeterministicPaperBroker()
    pending, entry_fill = broker.submit_stop(intent, _market(), stop_price=Decimal("6001.00"))
    assert entry_fill is None

    cancelled, cancel_fill = broker.cancel_pending_for_session_close(pending, intent, occurred_at=NOW)

    assert cancelled.order_id == pending.order_id
    assert cancelled.status == "CANCELLED"
    assert cancelled.order_type == "STOP"
    assert cancelled.stop_price == pending.stop_price
    assert cancel_fill is None
