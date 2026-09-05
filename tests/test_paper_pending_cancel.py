from datetime import datetime, timezone
from decimal import Decimal

import pytest

from trading_core.paper_execution import DeterministicPaperBroker, ExecutionConflict, MarketSnapshot, OrderIntent
from trading_core.paper_lifecycle import Bar


NOW = datetime(2026, 9, 3, 14, 30, tzinfo=timezone.utc)


def _intent(*, event_id: str, side: str = "LONG") -> OrderIntent:
    return OrderIntent(
        event_id=event_id,
        plan_id=event_id,
        plan_hash="pending-cancel",
        symbol="MES1!",
        side=side,
        quantity=1,
        expected_fill_price=Decimal("6000.25"),
        protective_stop_price=Decimal("5990.00") if side == "LONG" else Decimal("6010.00"),
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


def test_cancel_pending_limit_preserves_identity_and_emits_no_fill():
    intent = _intent(event_id="evt_mes_pending_cancel")
    broker = DeterministicPaperBroker()
    pending, entry_fill = broker.submit_limit(intent, _market(), limit_price=Decimal("5999.75"))
    assert entry_fill is None

    cancelled, cancel_fill = broker.cancel_pending(pending, intent, occurred_at=NOW)

    assert cancelled.order_id == pending.order_id
    assert cancelled.order_type == "LIMIT"
    assert cancelled.limit_price == pending.limit_price
    assert cancelled.status == "CANCELLED"
    assert cancel_fill is None


def test_cancel_pending_limit_fails_closed_after_same_order_already_filled():
    intent = _intent(event_id="evt_mes_limit_fill_then_cancel")
    broker = DeterministicPaperBroker()
    pending, _ = broker.submit_limit(intent, _market(), limit_price=Decimal("5999.75"))
    filled, fill = broker.process_pending_limit(
        pending,
        intent,
        bar_low=Decimal("5999.50"),
        bar_high=Decimal("6000.25"),
        occurred_at=NOW,
    )
    assert filled.status == "FILLED"
    assert fill is not None

    with pytest.raises(ExecutionConflict, match="already filled"):
        broker.cancel_pending(pending, intent, occurred_at=NOW)


def test_cancel_pending_stop_fails_closed_after_same_order_already_filled():
    intent = _intent(event_id="evt_mes_stop_fill_then_cancel")
    broker = DeterministicPaperBroker()
    pending, _ = broker.submit_stop(intent, _market(), stop_price=Decimal("6000.25"))
    filled, fill = broker.process_pending_stop(
        pending,
        intent,
        bar=Bar(open=Decimal("6000.50"), high=Decimal("6001.00"), low=Decimal("5999.75"), close=Decimal("6000.75")),
        occurred_at=NOW,
    )
    assert filled.status == "FILLED"
    assert fill is not None

    with pytest.raises(ExecutionConflict, match="already filled"):
        broker.cancel_pending(pending, intent, occurred_at=NOW)
