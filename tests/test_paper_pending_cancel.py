from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent


NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def test_cancel_pending_limit_preserves_identity_and_emits_no_fill():
    intent = OrderIntent(
        event_id="evt_mes_pending_cancel",
        plan_id="evt_mes_pending_cancel",
        plan_hash="pending-cancel",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        expected_fill_price=Decimal("6000.25"),
        protective_stop_price=Decimal("5990.00"),
        risk_usd=Decimal("53.75"),
        session_id="CME-2026-09-03",
        not_before=NOW,
    )
    market = MarketSnapshot(
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
    broker = DeterministicPaperBroker()
    pending, entry_fill = broker.submit_limit(intent, market, limit_price=Decimal("5999.75"))
    assert entry_fill is None

    cancelled, cancel_fill = broker.cancel_pending(pending, intent, occurred_at=NOW)

    assert cancelled.order_id == pending.order_id
    assert cancelled.order_type == "LIMIT"
    assert cancelled.limit_price == pending.limit_price
    assert cancelled.status == "CANCELLED"
    assert cancel_fill is None
