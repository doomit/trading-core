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


def test_future_next_bar_is_rejected_before_broker_execution():
    now = datetime(2026, 8, 31, 14, 31, tzinfo=timezone.utc)
    event_id = "evt_future_next_bar"
    document = {
        "schema": "trading_plan_v1",
        "plan_id": event_id,
        "trigger_event_id": event_id,
        "created_at": (now - timedelta(seconds=10)).isoformat(),
        "valid_until": (now + timedelta(minutes=2)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["anti-lookahead regression"],
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6005.00"},
        },
    }
    context = RiskContext(
        now=now,
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
            feed_as_of=now - timedelta(seconds=5),
            next_bar_start=now + timedelta(seconds=30),
            next_bar_open=Decimal("6000.00"),
            environment="PROD",
            data_class="REAL",
            source="tradingview",
            healthy=True,
            consecutive_closed_bars=3,
        ),
    )

    result = execute_reserved_plan(
        document,
        event_id=event_id,
        reservation_plan_hash=canonical_plan_hash(document),
        context=context,
        risk_gateway=RiskGateway(),
        broker=DeterministicPaperBroker(),
        ledger=ExecutionLedger(),
    )

    assert result.status == "REJECTED"
    assert result.reason_code == "NEXT_BAR_NOT_OBSERVED"
    assert result.order is None
    assert result.fill is None
