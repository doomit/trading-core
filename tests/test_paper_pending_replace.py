from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent


NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def test_replace_pending_limit_cancels_old_identity_and_creates_deterministic_new_pending_order():
    intent = OrderIntent(
        event_id="evt_mes_pending_replace",
        plan_id="evt_mes_pending_replace",
        plan_hash="pending-replace",
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

    cancelled, replacement, replace_fill = broker.replace_pending_limit(
        pending,
        intent,
        market,
        new_limit_price=Decimal("5999.50"),
        occurred_at=NOW,
    )

    assert cancelled.order_id == pending.order_id
    assert cancelled.status == "CANCELLED"
    assert replacement.order_id != pending.order_id
    assert replacement.status == "PENDING"
    assert replacement.order_type == "LIMIT"
    assert replacement.limit_price == Decimal("5999.50")
    assert replace_fill is None
