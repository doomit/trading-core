from __future__ import annotations

from importlib import import_module
from importlib.util import find_spec


def _ledger():
    assert find_spec("trading_core.simple_paper_ledger") is not None, "ledger module is required"
    return import_module("trading_core.simple_paper_ledger")


def _event(sequence: int, event_type: str, *, realized: float, action: str, price: float, qty: int, occurred_at: str):
    side = "BUY" if action in {"OPEN", "ADD"} else "SELL"
    execution = {
        "schema": "paper_execution_log_v2",
        "execution_id": f"exec-{sequence}",
        "cycle_id": f"cycle-{sequence}",
        "account_id": "paper-a",
        "account_type": "PAPER",
        "broker": "INTERNAL_PAPER",
        "environment": "paper",
        "symbol": "MES",
        "plan_id": "plan-open" if sequence == 1 else "plan-manage",
        "position_id": "paper-a-mes-001",
        "position_version_before": None if sequence == 1 else sequence - 2,
        "position_version_after": sequence - 1,
        "action": action,
        "side": side,
        "qty": qty,
        "price": price,
        "market_bar_end": occurred_at,
        "executed_at": occurred_at,
        "synthetic": False,
        "reason": f"{event_type}_TEST",
        "realized_pnl_usd": realized,
    }
    return {
        "schema": "position_event_v1",
        "event_id": f"pevt-{sequence}",
        "account_id": "paper-a",
        "account_type": "PAPER",
        "broker": "INTERNAL_PAPER",
        "environment": "paper",
        "symbol": "MES",
        "position_id": "paper-a-mes-001",
        "sequence": sequence,
        "event_type": event_type,
        "occurred_at": occurred_at,
        "plan_id": execution["plan_id"],
        "execution_id": execution["execution_id"],
        "position_before": {},
        "position_after": {"side": "LONG"},
        "account_before": {},
        "account_after": {},
        "execution": execution,
        "qty_delta": qty if action in {"OPEN", "ADD"} else -qty,
        "fill_price": price,
        "realized_pnl_delta_usd": realized,
        "reason": execution["reason"],
    }


def test_trade_ledger_projects_partial_reduction_and_terminal_pnl_exactly():
    ledger = _ledger()
    events = [
        _event(1, "POSITION_OPENED", realized=0.0, action="OPEN", price=6500.0, qty=2, occurred_at="2026-09-09T20:01:00Z"),
        _event(2, "POSITION_ADDED", realized=0.0, action="ADD", price=6502.0, qty=2, occurred_at="2026-09-09T20:03:00Z"),
        _event(3, "POSITION_REDUCED", realized=40.0, action="REDUCE", price=6505.0, qty=2, occurred_at="2026-09-09T20:05:00Z"),
        _event(4, "POSITION_EXITED", realized=60.0, action="EXIT", price=6507.0, qty=2, occurred_at="2026-09-09T20:07:00Z"),
    ]

    trade = ledger.project_trade_ledger(events)
    assert trade["schema"] == "trade_ledger_v1"
    assert trade["account_id"] == "paper-a"
    assert trade["position_id"] == "paper-a-mes-001"
    assert trade["side"] == "LONG"
    assert len(trade["entry_fills"]) == 1
    assert len(trade["add_fills"]) == 1
    assert len(trade["reduce_fills"]) == 1
    assert trade["exit_fill"]["price"] == 6507.0
    assert trade["gross_realized_pnl_usd"] == 100.0
    assert trade["fees_usd"] == 0.0
    assert trade["slippage_usd"] == 0.0
    assert trade["net_realized_pnl_usd"] == 100.0
    assert trade["opened_at"] == "2026-09-09T20:01:00Z"
    assert trade["closed_at"] == "2026-09-09T20:07:00Z"
    assert trade["duration_ms"] == 360000
    assert trade["event_ids"] == ["pevt-1", "pevt-2", "pevt-3", "pevt-4"]
    assert trade["execution_ids"] == ["exec-1", "exec-2", "exec-3", "exec-4"]
