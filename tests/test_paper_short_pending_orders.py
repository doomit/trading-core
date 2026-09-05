from datetime import datetime, timezone
from decimal import Decimal

from trading_core.paper_execution import DeterministicPaperBroker, MarketSnapshot, OrderIntent
from trading_core.paper_lifecycle import Bar


NOW = datetime(2026, 9, 5, 3, 47, tzinfo=timezone.utc)


def _short_intent() -> OrderIntent:
    return OrderIntent(
        event_id="evt_mes_short_pending_1",
        plan_id="evt_mes_short_pending_1",
        plan_hash="short-pending",
        symbol="MES1!",
        side="SHORT",
        quantity=1,
        expected_fill_price=Decimal("5999.75"),
        protective_stop_price=Decimal("6010.00"),
        risk_usd=Decimal("53.75"),
        session_id="CME-2026-09-04",
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


def test_pending_sell_limit_waits_then_fills_at_limit_and_replay_is_exactly_once():
    broker = DeterministicPaperBroker()
    intent = _short_intent()
    pending, entry_fill = broker.submit_limit(
        intent,
        _market(),
        limit_price=Decimal("6000.25"),
    )
    assert entry_fill is None

    waiting, waiting_fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.00"),
        occurred_at=NOW,
    )
    assert waiting == pending
    assert waiting_fill is None

    filled, fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.75"),
        bar_high=Decimal("6000.50"),
        occurred_at=NOW,
    )
    replayed, duplicate_fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.75"),
        bar_high=Decimal("6000.50"),
        occurred_at=NOW,
    )

    assert filled.status == "FILLED"
    assert fill is not None
    assert fill.price == Decimal("6000.25")
    assert fill.reference_price == Decimal("6000.25")
    assert fill.slippage_points == Decimal("0")
    assert fill.commission_usd == Decimal("1.25")
    assert replayed == filled
    assert duplicate_fill is None


def test_pending_sell_stop_waits_then_gap_down_fills_at_worse_open_and_replay_is_exactly_once():
    broker = DeterministicPaperBroker()
    intent = _short_intent()
    pending, entry_fill = broker.submit_stop(
        intent,
        _market(),
        stop_price=Decimal("5999.75"),
    )
    assert entry_fill is None

    waiting, waiting_fill = broker.process_pending_stop(
        pending,
        intent,
        bar=Bar(
            open=Decimal("6000.00"),
            high=Decimal("6000.25"),
            low=Decimal("6000.00"),
            close=Decimal("6000.00"),
        ),
        occurred_at=NOW,
    )
    assert waiting == pending
    assert waiting_fill is None

    trigger = Bar(
        open=Decimal("5999.00"),
        high=Decimal("5999.50"),
        low=Decimal("5998.75"),
        close=Decimal("5999.25"),
    )
    filled, fill = broker.process_pending_stop(pending, intent, bar=trigger, occurred_at=NOW)
    replayed, duplicate_fill = broker.process_pending_stop(pending, intent, bar=trigger, occurred_at=NOW)

    assert filled.status == "FILLED"
    assert fill is not None
    assert fill.price == Decimal("5999.00")
    assert fill.reference_price == Decimal("5999.75")
    assert fill.slippage_points == Decimal("-0.75")
    assert fill.commission_usd == Decimal("1.25")
    assert replayed == filled
    assert duplicate_fill is None
