from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_core.configurable_risk import ConfigurableRiskGateway
from trading_core.paper_execution import AccountState, MarketSnapshot, RiskContext, canonical_plan_hash, execute_reserved_plan, DeterministicPaperBroker, ExecutionLedger
from trading_core.runtime_config import TradingRuntimeConfig

NOW = datetime(2026, 9, 1, 5, 30, tzinfo=timezone.utc)
EVENT = "evt_cfg_risk_001"


def config_doc(**risk_overrides):
    risk = {
        "target_risk_per_trade_usd": 500,
        "max_risk_per_trade_usd": 1000,
        "max_open_micro_contracts": 20,
        "max_daily_realized_loss_usd": 5000,
        "max_consecutive_losses": 4,
        "max_entries_per_session": 30,
    }
    risk.update(risk_overrides)
    return {
        "schema": "trading_runtime_config_v1",
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "created_at": "2026-09-01T05:00:00+00:00",
        "paper_only": True,
        "account": {"starting_equity_usd": 1_000_000},
        "session": {"profile": "CME_INDEX_23H", "timezone": "America/Chicago", "maintenance_start": "16:00", "maintenance_end": "17:00"},
        "strategy": {"profile": "PA_AGGRESSIVE_A2", "analysis_timeframe": "5m", "min_action_confidence": 0.60, "setup_families": ["EMA_VWAP_FIRST_PULLBACK", "FAILED_BREAKOUT_REVERSAL"]},
        "risk": risk,
    }


def cfg(**risk_overrides):
    return TradingRuntimeConfig.from_document(config_doc(**risk_overrides))


def account(**overrides):
    values = {
        "mode": "PAPER",
        "starting_equity_usd": Decimal("1000000"),
        "equity_usd": Decimal("1000000"),
        "daily_realized_pnl_usd": Decimal("0"),
        "consecutive_failures": 0,
        "open_contracts_total": 0,
        "entries_this_session": 0,
    }
    values.update(overrides)
    return AccountState(**values)


def market(**overrides):
    values = {
        "symbol": "MES1!",
        "feed_as_of": NOW - timedelta(seconds=5),
        "next_bar_start": NOW,
        "next_bar_open": Decimal("6000.00"),
        "environment": "PROD",
        "data_class": "REAL",
        "source": "tradingview",
        "healthy": True,
        "consecutive_closed_bars": 3,
    }
    values.update(overrides)
    return MarketSnapshot(**values)


def context(*, account_state=None, market_state=None, **overrides):
    values = {
        "now": NOW,
        "session_id": "CME-2026-09-01",
        "session_open": True,
        "kill_switch": False,
        "account": account_state or account(),
        "market": market_state or market(),
    }
    values.update(overrides)
    return RiskContext(**values)


def plan(**overrides):
    document = {
        "schema": "trading_plan_v1",
        "plan_id": EVENT,
        "trigger_event_id": EVENT,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=50)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.67,
        "analysis_summary": ["first EMA pullback"],
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "setup_family": "EMA_VWAP_FIRST_PULLBACK",
        "risk_budget_usd": 500,
        "position_action": {
            "quantity": 5,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6010.00"},
        },
    }
    document.update(overrides)
    return document


def evaluate(document=None, *, config=None, risk_context=None):
    document = document or plan()
    return ConfigurableRiskGateway(config or cfg()).evaluate(
        document,
        event_id=EVENT,
        plan_hash=canonical_plan_hash(document),
        context=risk_context or context(),
    )


def test_valid_1m_account_a2_plan_is_risk_approved_with_brain_selected_quantity():
    decision = evaluate()
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"
    assert decision.intent.quantity == 5
    assert decision.intent.risk_usd == Decimal("268.75")


@pytest.mark.parametrize(
    "document,reason",
    [
        (plan(config_version="cfg_old"), "CONFIG_VERSION_MISMATCH"),
        (plan(strategy_profile="PA_OTHER"), "STRATEGY_PROFILE_MISMATCH"),
        (plan(confidence=0.59), "CONFIDENCE_BELOW_CONFIG_MIN"),
        (plan(risk_budget_usd=1001), "RISK_BUDGET_EXCEEDS_CONFIG_MAX"),
        (plan(position_action={"quantity": 1, "protective_stop": {"price": "5990"}}), "MISSING_TAKE_PROFIT"),
    ],
)
def test_plan_must_bind_exact_active_config_and_complete_directional_risk_contract(document, reason):
    assert evaluate(document).reason_code == reason


def test_actual_risk_is_downsized_to_plan_budget_instead_of_rejected():
    document = plan(position_action={"quantity": 10, "protective_stop": {"price": "5990"}, "take_profit": {"price": "6010"}})
    decision = evaluate(document)
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"
    assert decision.intent.quantity == 9
    assert decision.intent.risk_usd == Decimal("483.75")


def test_actual_risk_is_downsized_to_config_hard_cap_instead_of_rejected():
    document = plan(
        risk_budget_usd=1000,
        position_action={"quantity": 20, "protective_stop": {"price": "5990"}, "take_profit": {"price": "6010"}},
    )
    decision = evaluate(document)
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"
    assert decision.intent.quantity == 18
    assert decision.intent.risk_usd == Decimal("967.50")


def test_requested_quantity_is_downsized_to_remaining_config_capacity():
    document = plan(
        risk_budget_usd=1000,
        position_action={"quantity": 20, "protective_stop": {"price": "5998.25"}, "take_profit": {"price": "6005"}},
    )
    decision = evaluate(document, risk_context=context(account_state=account(open_contracts_total=1)))
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"
    assert decision.intent.quantity == 19
    assert decision.intent.risk_usd == Decimal("237.50")


def test_one_micro_that_exceeds_plan_budget_is_still_rejected_fail_closed():
    document = plan(
        risk_budget_usd=10,
        position_action={"quantity": 5, "protective_stop": {"price": "5990"}, "take_profit": {"price": "6010"}},
    )
    decision = evaluate(document)
    assert decision.approved is False
    assert decision.reason_code == "PLAN_RISK_BUDGET_EXCEEDED"


@pytest.mark.parametrize(
    "account_state,reason",
    [
        (account(starting_equity_usd=Decimal("50000")), "UNAUTHORIZED_ACCOUNT"),
        (account(daily_realized_pnl_usd=Decimal("-5000")), "DAILY_LOSS_LIMIT_REACHED"),
        (account(consecutive_failures=4), "CONSECUTIVE_FAILURE_LIMIT_REACHED"),
        (account(entries_this_session=30), "SESSION_ENTRY_LIMIT_REACHED"),
    ],
)
def test_account_limits_come_from_runtime_config(account_state, reason):
    assert evaluate(risk_context=context(account_state=account_state)).reason_code == reason


def test_take_profit_direction_is_validated_against_observed_fill():
    long_bad = plan(position_action={"quantity": 1, "protective_stop": {"price": "5998"}, "take_profit": {"price": "6000"}})
    assert evaluate(long_bad).reason_code == "INVALID_TAKE_PROFIT_DIRECTION"


def test_existing_executor_accepts_configurable_gateway_without_changing_exactly_once_path():
    document = plan()
    result = execute_reserved_plan(
        document,
        event_id=EVENT,
        reservation_plan_hash=canonical_plan_hash(document),
        context=context(),
        risk_gateway=ConfigurableRiskGateway(cfg()),
        broker=DeterministicPaperBroker(),
        ledger=ExecutionLedger(),
    )
    assert result.status == "FILLED"
    assert result.position is not None and result.position.quantity == 5
