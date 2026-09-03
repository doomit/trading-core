from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.paper_execution import (
    AccountState,
    DeterministicPaperBroker,
    ExecutionLedger,
    MarketSnapshot,
    RiskContext,
    RiskGateway,
    canonical_plan_hash,
    execute_reserved_plan,
)


NOW = datetime(2026, 8, 31, 14, 31, tzinfo=timezone.utc)
EVENT_ID = "evt_mes_contract_20260831T143000Z_0001"


def _plan():
    return {
        "schema": "trading_plan_v1",
        "plan_id": EVENT_ID,
        "trigger_event_id": EVENT_ID,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=50)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["correlated execution-contract regression"],
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6005.00"},
        },
    }


def _context():
    return RiskContext(
        now=NOW,
        session_id="CME-2026-08-31",
        session_open=True,
        kill_switch=False,
        account=AccountState(
            mode="PAPER",
            starting_equity_usd=Decimal("50000.00"),
            equity_usd=Decimal("50000.00"),
            daily_realized_pnl_usd=Decimal("0.00"),
            consecutive_failures=0,
            open_contracts_total=0,
        ),
        market=MarketSnapshot(
            symbol="MES1!",
            feed_as_of=NOW - timedelta(seconds=5),
            next_bar_start=NOW,
            next_bar_open=Decimal("6000.00"),
            environment="PROD",
            data_class="REAL",
            source="tradingview",
            healthy=True,
            consecutive_closed_bars=3,
        ),
    )


def _execute():
    document = _plan()
    return execute_reserved_plan(
        document,
        event_id=EVENT_ID,
        reservation_plan_hash=canonical_plan_hash(document),
        context=_context(),
        risk_gateway=RiskGateway(),
        broker=DeterministicPaperBroker(),
        ledger=ExecutionLedger(),
    )


def test_filled_entry_exposes_deterministic_correlated_position_and_trade_records():
    first = _execute()
    second = _execute()

    assert first.status == "FILLED"
    assert first.position == second.position
    assert first.trade == second.trade

    assert first.position.event_id == EVENT_ID
    assert first.position.plan_id == EVENT_ID
    assert first.position.order_id == first.order.order_id
    assert first.position.entry_fill_id == first.fill.fill_id
    assert first.position.symbol == "MES1!"
    assert first.position.side == "LONG"
    assert first.position.quantity == 1
    assert first.position.entry_price == first.fill.price
    assert first.position.opened_at == first.fill.occurred_at
    assert first.position.status == "OPEN"

    assert first.trade.event_id == EVENT_ID
    assert first.trade.plan_id == EVENT_ID
    assert first.trade.order_id == first.order.order_id
    assert first.trade.fill_id == first.fill.fill_id
    assert first.trade.position_id == first.position.position_id
    assert first.trade.symbol == "MES1!"
    assert first.trade.side == "LONG"
    assert first.trade.quantity == 1
    assert first.trade.price == first.fill.price
    assert first.trade.occurred_at == first.fill.occurred_at
    assert first.trade.role == "ENTRY"


def test_position_and_trade_ids_are_distinct_but_stable():
    first = _execute()
    second = _execute()

    assert first.position.position_id == second.position.position_id
    assert first.trade.trade_id == second.trade.trade_id
    assert first.position.position_id != first.trade.trade_id


def test_entry_fill_persists_deterministic_slippage_and_commission_economics():
    result = _execute()

    assert result.fill.reference_price == Decimal("6000.00")
    assert result.fill.slippage_points == Decimal("0.25")
    assert result.fill.commission_usd == Decimal("1.25")
    assert result.trade.slippage_points == result.fill.slippage_points
    assert result.trade.commission_usd == result.fill.commission_usd
