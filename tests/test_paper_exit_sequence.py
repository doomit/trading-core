from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.paper_execution import ExecutionResult, PaperPositionRecord, canonical_plan_hash
from trading_core.paper_exit import advance_open_position_through_bars
from trading_core.paper_lifecycle import Bar

NOW = datetime(2026, 9, 4, 19, 30, tzinfo=timezone.utc)
EVENT_ID = "evt_sequence_partial_then_stop"


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
        "analysis_summary": ["stateful multi-bar lifecycle"],
        "position_action": {
            "quantity": 3,
            "target_exit_quantity": 1,
            "protective_stop": {"price": "5995.00"},
            "take_profit": {"price": "6005.00"},
        },
    }


def _entry(plan):
    return ExecutionResult(
        event_id=EVENT_ID,
        plan_id=EVENT_ID,
        plan_hash=canonical_plan_hash(plan),
        status="OPEN",
        reason_code="PAPER_ENTRY_FILLED_POSITION_OPEN",
        terminal=False,
        receipts=(),
        position=PaperPositionRecord(
            "paper-position:sequence",
            EVENT_ID,
            EVENT_ID,
            "paper-order:sequence",
            "paper-fill:sequence",
            "MES1!",
            "LONG",
            3,
            Decimal("6000.00"),
            NOW,
            "OPEN",
        ),
    )


def _partial_then_stop_result():
    plan = _plan()
    bars = [
        (
            Bar(open=Decimal("6001"), high=Decimal("6006"), low=Decimal("6000"), close=Decimal("6004")),
            NOW + timedelta(minutes=5),
        ),
        (
            Bar(open=Decimal("5998"), high=Decimal("6000"), low=Decimal("5994"), close=Decimal("5996")),
            NOW + timedelta(minutes=10),
        ),
    ]
    return advance_open_position_through_bars(plan, _entry(plan), bars)


def test_sequence_carries_partial_exit_state_into_next_bar():
    result = _partial_then_stop_result()

    assert result.terminal is True
    assert result.status == "CLOSED"
    assert result.reason_code == "STOP_FILLED"
    assert result.position is not None and result.position.quantity == 0
    assert result.fill is not None and result.fill.quantity == 2
    assert [receipt["reason_code"] for receipt in result.receipts] == [
        "TARGET_PARTIAL_FILLED",
        "STOP_FILLED",
        "STOP_FILLED",
    ]


def test_sequence_assigns_distinct_ids_to_distinct_exit_fill_receipts():
    result = _partial_then_stop_result()
    exit_receipts = [
        receipt
        for receipt in result.receipts
        if receipt["stage"] == "PAPER_EXIT_FILLED"
    ]

    assert len(exit_receipts) == 2
    assert len({receipt["receipt_id"] for receipt in exit_receipts}) == 2
