from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.paper_execution import ExecutionResult, PaperPositionRecord, canonical_plan_hash
from trading_core.paper_exit import close_open_position
from trading_core.paper_lifecycle import Bar


NOW = datetime(2026, 9, 3, 5, 20, tzinfo=timezone.utc)
EVENT_ID = "evt_mes_partial_durable_001"


def _plan():
    return {
        "schema": "trading_plan_v1",
        "plan_id": EVENT_ID,
        "trigger_event_id": EVENT_ID,
        "created_at": (NOW - timedelta(minutes=1)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=30)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["durable partial-exit adapter regression"],
        "position_action": {
            "quantity": 3,
            "target_exit_quantity": 1,
            "protective_stop": {"price": "5995.00"},
            "take_profit": {"price": "6005.00"},
        },
    }


def _three_lot_open_entry(plan):
    plan_hash = canonical_plan_hash(plan)
    position = PaperPositionRecord(
        position_id="paper-position:partial-durable",
        event_id=EVENT_ID,
        plan_id=EVENT_ID,
        order_id="paper-order:partial-durable",
        entry_fill_id="paper-fill:partial-durable",
        symbol="MES1!",
        side="LONG",
        quantity=3,
        entry_price=Decimal("6000.00"),
        opened_at=NOW,
        status="OPEN",
    )
    return ExecutionResult(
        event_id=EVENT_ID,
        plan_id=EVENT_ID,
        plan_hash=plan_hash,
        status="FILLED",
        reason_code="PAPER_ENTRY_FILLED_POSITION_OPEN",
        terminal=False,
        receipts=(),
        position=position,
    )


def test_partial_target_persists_remaining_position_and_final_stop_correlates_all_exits():
    plan = _plan()
    entry = _three_lot_open_entry(plan)
    target_bar = Bar(
        open=Decimal("6001.00"),
        high=Decimal("6006.00"),
        low=Decimal("6000.00"),
        close=Decimal("6004.00"),
    )

    partial = close_open_position(plan, entry, target_bar, occurred_at=NOW + timedelta(minutes=5))

    assert partial.terminal is False
    assert partial.status == "OPEN"
    assert partial.reason_code == "TARGET_PARTIAL_FILLED"
    assert partial.position is not None
    assert partial.position.position_id == entry.position.position_id
    assert partial.position.quantity == 2
    assert partial.position.status == "OPEN"
    assert partial.fill is not None and partial.fill.quantity == 1
    assert partial.fill.reference_price == Decimal("6005.00")
    assert partial.fill.slippage_points == Decimal("0.00")
    assert partial.fill.commission_usd == Decimal("1.25")
    assert partial.trade is not None and partial.trade.role == "EXIT"
    assert partial.trade.position_id == entry.position.position_id
    assert partial.trade.quantity == 1
    assert [receipt["stage"] for receipt in partial.receipts] == ["PAPER_EXIT_FILLED"]

    repeated_target = close_open_position(
        plan,
        partial,
        target_bar,
        occurred_at=NOW + timedelta(minutes=10),
    )
    assert repeated_target == partial

    stop_bar = Bar(
        open=Decimal("5998.00"),
        high=Decimal("6000.00"),
        low=Decimal("5994.00"),
        close=Decimal("5996.00"),
    )
    final = close_open_position(
        plan,
        partial,
        stop_bar,
        occurred_at=NOW + timedelta(minutes=15),
    )

    assert final.terminal is True
    assert final.status == "CLOSED"
    assert final.reason_code == "STOP_FILLED"
    assert final.position is not None
    assert final.position.position_id == entry.position.position_id
    assert final.position.quantity == 0
    assert final.position.status == "CLOSED"
    assert final.fill is not None and final.fill.quantity == 2
    assert final.fill.reference_price == Decimal("5995.00")
    assert final.fill.commission_usd == Decimal("2.50")
    assert final.trade is not None
    assert final.trade.position_id == entry.position.position_id
    assert final.trade.quantity == 2
    assert [receipt["stage"] for receipt in final.receipts] == [
        "PAPER_EXIT_FILLED",
        "PAPER_EXIT_FILLED",
        "COMPLETED",
    ]
    assert all(receipt["event_id"] == EVENT_ID for receipt in final.receipts)
    assert all(receipt["plan_id"] == EVENT_ID for receipt in final.receipts)
