from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.paper_bracket import PaperBracketRelationship, build_paper_bracket
from trading_core.paper_execution import ExecutionResult, PaperPositionRecord, canonical_plan_hash
from trading_core.paper_exit import advance_open_position_through_bars_with_bracket
from trading_core.paper_lifecycle import Bar

NOW = datetime(2026, 9, 5, 12, 15, tzinfo=timezone.utc)
EVENT_ID = "evt_durable_bracket_exit_state"


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
        "analysis_summary": ["durable bracket state across restart"],
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
            "paper-position:durable-bracket",
            EVENT_ID,
            EVENT_ID,
            "paper-order:durable-bracket",
            "paper-fill:durable-bracket",
            "MES1!",
            "LONG",
            3,
            Decimal("6000.00"),
            NOW,
            "OPEN",
        ),
    )


def test_partial_target_advances_authoritative_bracket_and_survives_restart():
    plan = _plan()
    entry = _entry(plan)
    bracket = build_paper_bracket(
        parent_order_id=entry.position.order_id,
        quantity=3,
        target_quantity=1,
    )
    target_bar = (
        Bar(open=Decimal("6001"), high=Decimal("6006"), low=Decimal("6000"), close=Decimal("6004")),
        NOW + timedelta(minutes=5),
    )

    partial, updated = advance_open_position_through_bars_with_bracket(plan, entry, bracket, [target_bar])

    assert partial.terminal is False
    assert partial.position is not None and partial.position.quantity == 2
    assert partial.order is not None and partial.order.order_id == bracket.target_order_id
    assert updated.remaining_quantity == 2
    assert updated.target_filled_quantity == 1
    assert updated.target_order_id in updated.filled_order_ids
    assert updated.stop_order_id not in updated.cancelled_order_ids
    assert updated.active_stop_quantity == 2
    assert updated.active_target_quantity == 0

    restored = PaperBracketRelationship.from_record(updated.to_record())
    assert restored == updated


def test_restored_bracket_is_authoritative_for_later_stop_and_closes_oco():
    plan = _plan()
    entry = _entry(plan)
    bracket = build_paper_bracket(
        parent_order_id=entry.position.order_id,
        quantity=3,
        target_quantity=1,
    )
    target_bar = (
        Bar(open=Decimal("6001"), high=Decimal("6006"), low=Decimal("6000"), close=Decimal("6004")),
        NOW + timedelta(minutes=5),
    )
    partial, updated = advance_open_position_through_bars_with_bracket(plan, entry, bracket, [target_bar])
    restored = PaperBracketRelationship.from_record(updated.to_record())
    stop_bar = (
        Bar(open=Decimal("5998"), high=Decimal("6000"), low=Decimal("5994"), close=Decimal("5996")),
        NOW + timedelta(minutes=10),
    )

    result, closed = advance_open_position_through_bars_with_bracket(plan, partial, restored, [stop_bar])

    assert result.terminal is True
    assert result.reason_code == "STOP_FILLED"
    assert result.position is not None and result.position.quantity == 0
    assert result.order is not None and result.order.order_id == bracket.stop_order_id
    assert result.fill is not None and result.fill.quantity == 2
    assert closed.status == "CLOSED"
    assert closed.remaining_quantity == 0
    assert bracket.stop_order_id in closed.filled_order_ids
    assert bracket.target_order_id in closed.filled_order_ids
    assert closed.cancelled_order_ids == ()
