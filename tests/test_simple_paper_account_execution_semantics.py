import pytest

from trading_core.simple_paper_contracts import (
    validate_paper_account_state_semantics,
    validate_paper_execution_semantics,
)


def _account(**overrides) -> dict:
    value = {
        "schema": "paper_account_state_v1",
        "account_id": "simple-paper-v1",
        "mode": "PAPER",
        "starting_balance_usd": 10000000.0,
        "realized_pnl_usd": 125.0,
        "balance_usd": 10000125.0,
        "closed_action_count": 3,
        "updated_at": "2026-09-07T17:30:15Z",
        "last_execution_id": "exec-mes-173015-reduce",
    }
    value.update(overrides)
    return value


def _execution(action: str = "REDUCE", **overrides) -> dict:
    value = {
        "schema": "paper_execution_log_v1",
        "execution_id": "exec-mes-173015-reduce",
        "cycle_id": "cycle-20260907-173015",
        "symbol": "MES",
        "plan_id": "plan-mes-1715",
        "position_id": "MES-20260907-0907-01",
        "position_version_before": 4,
        "position_version_after": 5,
        "action": action,
        "side": "SELL",
        "qty": 1,
        "price": 6510.25,
        "market_bar_end": "2026-09-07T17:30:00Z",
        "executed_at": "2026-09-07T17:30:15Z",
        "synthetic": False,
        "reason": "PLAN_REDUCE_TRIGGER",
        "realized_pnl_usd": 50.0,
    }
    value.update(overrides)
    return value


def test_paper_account_balance_must_equal_starting_balance_plus_realized_pnl():
    validate_paper_account_state_semantics(_account())

    with pytest.raises(ValueError, match="balance_usd"):
        validate_paper_account_state_semantics(_account(balance_usd=10000999.0))


def test_open_execution_creates_version_zero_from_no_previous_position_version():
    value = _execution(
        "OPEN",
        position_version_before=None,
        position_version_after=0,
        side="BUY",
        qty=4,
        realized_pnl_usd=0.0,
        reason="PLAN_OPEN_TRIGGER",
    )
    validate_paper_execution_semantics(value)

    with pytest.raises(ValueError, match="OPEN"):
        validate_paper_execution_semantics(_execution("OPEN", position_version_before=0, position_version_after=1))


def test_existing_position_execution_increments_version_exactly_once():
    for action in ["ADD", "REDUCE", "EXIT", "STOP", "TAKE_PROFIT", "EOD_FORCED_CLOSE", "STALE_FEED_FORCED_STOP"]:
        overrides = {"realized_pnl_usd": 0.0} if action == "ADD" else {}
        if action == "STALE_FEED_FORCED_STOP":
            overrides["synthetic"] = True
        validate_paper_execution_semantics(_execution(action, **overrides))

    with pytest.raises(ValueError, match="position_version_after"):
        validate_paper_execution_semantics(_execution("REDUCE", position_version_before=4, position_version_after=6))


def test_add_execution_must_not_realize_pnl():
    validate_paper_execution_semantics(_execution("ADD", side="BUY", realized_pnl_usd=0.0))

    with pytest.raises(ValueError, match="ADD"):
        validate_paper_execution_semantics(_execution("ADD", side="BUY", realized_pnl_usd=25.0))


def test_stale_feed_forced_stop_must_be_synthetic():
    validate_paper_execution_semantics(_execution("STALE_FEED_FORCED_STOP", synthetic=True))

    with pytest.raises(ValueError, match="synthetic"):
        validate_paper_execution_semantics(_execution("STALE_FEED_FORCED_STOP", synthetic=False))
