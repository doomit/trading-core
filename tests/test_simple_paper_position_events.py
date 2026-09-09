from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from importlib import import_module
from importlib.util import find_spec

from trading_core.simple_paper_contracts import new_paper_account
from trading_core.simple_paper_position_execution import (
    execute_flat_plan,
    new_flat_position,
)


NOW = datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc)


def _ledger():
    assert find_spec("trading_core.simple_paper_ledger") is not None, "ledger module is required"
    return import_module("trading_core.simple_paper_ledger")


def _open_plan() -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": "plan-mes-open-event",
        "symbol": "MES",
        "analysis_bar_end": "2026-09-09T20:00:00Z",
        "generated_at": "2026-09-09T20:00:02Z",
        "action_valid_until": "2026-09-09T20:15:00Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "OPEN",
        "side": "LONG",
        "confidence": 0.8,
        "analysis_summary": ["event test"],
        "entry": {"order_type": "MARKET", "trigger_price": None, "qty": 2},
        "protection": {"stop_loss": 6490.0, "take_profit": 6520.0},
        "add_once": None,
        "reduce_once": None,
    }


def _open_transition():
    before_position = new_flat_position("MES", NOW, account_id="paper-a")
    before_account = new_paper_account("paper-a", NOW)
    transition = execute_flat_plan(
        current_position=before_position,
        accepted_plan=_open_plan(),
        market={
            "latest_bar_end": "2026-09-09T20:01:00Z",
            "bars": [{"end": "2026-09-09T20:01:00Z", "high": 6504.0, "low": 6498.0, "close": 6501.25}],
        },
        executed_at=datetime(2026, 9, 9, 20, 1, 5, tzinfo=timezone.utc),
        cycle_id="cycle-open-event",
        account=before_account,
        position_id="paper-a-mes-001",
        point_value=5.0,
    )
    return before_account, before_position, transition


def test_open_transition_derives_deterministic_sequence_one_event():
    ledger = _ledger()
    before_account, before_position, transition = _open_transition()
    first = ledger.derive_position_event(
        account_before=before_account,
        position_before=before_position,
        transition=transition,
    )
    second = ledger.derive_position_event(
        account_before=before_account,
        position_before=before_position,
        transition=transition,
    )

    assert first["schema"] == "position_event_v1"
    assert first["event_type"] == "POSITION_OPENED"
    assert first["account_id"] == "paper-a"
    assert first["position_id"] == "paper-a-mes-001"
    assert first["sequence"] == 1
    assert first["event_id"] == second["event_id"]
    assert first["position_after"]["event_sequence"] == 1
    assert first["position_after"]["last_event_id"] == first["event_id"]
    assert first["realized_pnl_delta_usd"] == 0.0
    ledger.validate_position_event_semantics(first)


def test_protection_only_update_is_event_even_without_execution():
    ledger = _ledger()
    before_account, _, opened = _open_transition()
    before = deepcopy(opened["position"])
    before["event_sequence"] = 1
    before["last_event_id"] = "pevt-open"
    after = deepcopy(before)
    after["stop_loss"] = 6498.0
    after["take_profit"] = 6525.0
    after["position_version"] = 1
    after["updated_at"] = "2026-09-09T20:02:00Z"
    transition = {
        "position": after,
        "account": deepcopy(opened["account"]),
        "execution": None,
        "outcome": "NO_ACTION",
        "changed": True,
        "synthetic": False,
        "stale_feed": False,
    }

    event = ledger.derive_position_event(
        account_before=opened["account"],
        position_before=before,
        transition=transition,
    )
    assert event["event_type"] == "PROTECTION_UPDATED"
    assert event["execution"] is None
    assert event["execution_id"] is None
    assert event["sequence"] == 2
    assert event["position_after"]["stop_loss"] == 6498.0


def test_heartbeat_only_timestamp_refresh_creates_no_semantic_event():
    ledger = _ledger()
    before_account, before_position, _ = _open_transition()
    after = deepcopy(before_position)
    after["updated_at"] = "2026-09-09T20:01:00Z"
    transition = {
        "position": after,
        "account": deepcopy(before_account),
        "execution": None,
        "outcome": "NO_ACTION",
        "changed": True,
        "synthetic": False,
        "stale_feed": False,
    }
    assert ledger.derive_position_event(
        account_before=before_account,
        position_before=before_position,
        transition=transition,
    ) is None
