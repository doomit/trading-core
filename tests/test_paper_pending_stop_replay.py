from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent
from trading_core.paper_lifecycle import Bar


NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def test_replayed_crossing_bar_does_not_emit_a_second_pending_stop_fill():
    intent = OrderIntent(
        event_id="evt_mes_pending_stop_replay",
        plan_id="evt_mes_pending_stop_replay",
        plan_hash="stop-replay",
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
    pending, entry_fill = broker.submit_stop(intent, market, stop_price=Decimal("6000.25"))
    assert entry_fill is None
    trigger = Bar(
        open=Decimal("6000.00"),
        high=Decimal("6000.50"),
        low=Decimal("5999.75"),
        close=Decimal("6000.25"),
    )

    filled, first_fill = broker.process_pending_stop(pending, intent, bar=trigger, occurred_at=NOW)
    replayed, duplicate_fill = broker.process_pending_stop(pending, intent, bar=trigger, occurred_at=NOW)

    assert first_fill is not None
    assert replayed == filled
    assert duplicate_fill is None
