from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.paper_execution import (
    AccountState,
    MarketSnapshot,
    RiskContext,
    RiskGateway,
    canonical_plan_hash,
)


def test_reserved_exposure_blocks_distinct_plan_before_order_intent():
    now = datetime(2026, 9, 3, 10, 16, tzinfo=timezone.utc)
    plan = {
        "schema": "trading_plan_v1",
        "plan_id": "evt_reserved_conflict",
        "trigger_event_id": "evt_reserved_conflict",
        "created_at": (now - timedelta(seconds=5)).isoformat(),
        "valid_until": (now + timedelta(seconds=55)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6005.00"},
        },
    }
    context = RiskContext(
        now=now,
        session_id="CME-2026-09-03",
        session_open=True,
        kill_switch=False,
        account=AccountState(
            mode="PAPER",
            starting_equity_usd=Decimal("50000.00"),
            equity_usd=Decimal("50000.00"),
            daily_realized_pnl_usd=Decimal("0.00"),
            consecutive_failures=0,
            open_contracts_total=0,
            reserved_contracts_total=1,
        ),
        market=MarketSnapshot(
            symbol="MES1!",
            feed_as_of=now - timedelta(seconds=5),
            next_bar_start=now,
            next_bar_open=Decimal("6000.00"),
            environment="PROD",
            data_class="REAL",
            source="tradingview",
            healthy=True,
            consecutive_closed_bars=3,
        ),
    )

    decision = RiskGateway().evaluate(
        plan,
        event_id=plan["trigger_event_id"],
        plan_hash=canonical_plan_hash(plan),
        context=context,
    )

    assert decision.approved is False
    assert decision.reason_code == "OPEN_ORDER_CONFLICT"
    assert decision.intent is None
