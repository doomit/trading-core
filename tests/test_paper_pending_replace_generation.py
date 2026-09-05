from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent
from trading_core.paper_lifecycle import Bar

NOW = datetime(2026, 9, 5, 9, 4, tzinfo=timezone.utc)


def _intent(*, event_id: str) -> OrderIntent:
    return OrderIntent(
        event_id=event_id,
        plan_id=event_id,
        plan_hash="pending-replace-generation",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        expected_fill_price=Decimal("6000.25"),
        protective_stop_price=Decimal("5990.00"),
        risk_usd=Decimal("53.75"),
        session_id="CME-2026-09-05",
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


def test_limit_replace_back_to_prior_price_gets_fresh_identity_and_can_fill():
    intent = _intent(event_id="evt_limit_replace_generation")
    broker = DeterministicPaperBroker()
    original, _ = broker.submit_limit(intent, _market(), limit_price=Decimal("5999.75"))

    _, replacement_1, _ = broker.replace_pending_limit(
        original, intent, _market(), new_limit_price=Decimal("5999.50"), occurred_at=NOW
    )
    _, replacement_2, _ = broker.replace_pending_limit(
        replacement_1, intent, _market(), new_limit_price=Decimal("5999.75"), occurred_at=NOW
    )

    assert len({original.order_id, replacement_1.order_id, replacement_2.order_id}) == 3
    filled, fill = broker.process_pending_limit(
        replacement_2,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.25"),
        occurred_at=NOW,
    )
    assert filled.status == "FILLED"
    assert fill is not None


def test_stop_replace_back_to_prior_price_gets_fresh_identity_and_can_fill():
    intent = _intent(event_id="evt_stop_replace_generation")
    broker = DeterministicPaperBroker()
    original, _ = broker.submit_stop(intent, _market(), stop_price=Decimal("6000.25"))

    _, replacement_1, _ = broker.replace_pending_stop(
        original, intent, _market(), new_stop_price=Decimal("6000.50"), occurred_at=NOW
    )
    _, replacement_2, _ = broker.replace_pending_stop(
        replacement_1, intent, _market(), new_stop_price=Decimal("6000.25"), occurred_at=NOW
    )

    assert len({original.order_id, replacement_1.order_id, replacement_2.order_id}) == 3
    filled, fill = broker.process_pending_stop(
        replacement_2,
        intent,
        bar=Bar(
            open=Decimal("6000.50"),
            high=Decimal("6001.00"),
            low=Decimal("5999.75"),
            close=Decimal("6000.75"),
        ),
        occurred_at=NOW,
    )
    assert filled.status == "FILLED"
    assert fill is not None
