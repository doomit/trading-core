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

NOW = datetime(2026, 9, 4, 1, 5, tzinfo=timezone.utc)
EVENT_ID = "evt_presubmit_guard"


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
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6005.00"},
        },
    }


def _context():
    return RiskContext(
        now=NOW,
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


class CountingBroker(DeterministicPaperBroker):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def submit(self, intent, market_state):
        self.calls += 1
        return super().submit(intent, market_state)


def test_presubmit_guard_can_fail_closed_after_risk_approval_without_calling_broker():
    document = _plan()
    broker = CountingBroker()
    seen = []

    def guard(intent):
        seen.append(intent)
        return "PAUSE_ACTIVE"

    result = execute_reserved_plan(
        document,
        event_id=EVENT_ID,
        reservation_plan_hash=canonical_plan_hash(document),
        context=_context(),
        risk_gateway=RiskGateway(),
        broker=broker,
        ledger=ExecutionLedger(),
        pre_submit_guard=guard,
    )

    assert len(seen) == 1
    assert seen[0].plan_id == EVENT_ID
    assert result.status == "REJECTED"
    assert result.reason_code == "PAUSE_ACTIVE"
    assert result.order is None
    assert result.fill is None
    assert broker.calls == 0
    assert [receipt["stage"] for receipt in result.receipts] == [
        "EXECUTOR_RECEIVED",
        "RISK_DECIDED",
        "PRE_SUBMIT_ADMISSION",
    ]
    assert result.receipts[-1]["status"] == "REJECTED"
