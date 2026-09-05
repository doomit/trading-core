from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.configurable_risk import ConfigurableRiskGateway
from trading_core.paper_execution import AccountState, MarketSnapshot, RiskContext, canonical_plan_hash
from trading_core.runtime_config import TradingRuntimeConfig


NOW = datetime(2026, 9, 5, 9, 45, tzinfo=timezone.utc)
EVENT = "evt_gate9_account_loss"


def _config() -> TradingRuntimeConfig:
    return TradingRuntimeConfig.from_document(
        {
            "schema": "trading_runtime_config_v1",
            "config_version": "cfg_gate9_account_loss",
            "created_at": "2026-09-05T09:00:00+00:00",
            "paper_only": True,
            "account": {"starting_equity_usd": 100000},
            "session": {
                "profile": "CME_INDEX_23H",
                "timezone": "America/Chicago",
                "maintenance_start": "16:00",
                "maintenance_end": "17:00",
            },
            "strategy": {
                "profile": "PA_AGGRESSIVE_A2",
                "analysis_timeframe": "1m",
                "min_action_confidence": 0.6,
                "setup_families": ["FAILED_BREAKOUT_REVERSAL"],
            },
            "risk": {
                "target_risk_per_trade_usd": 500,
                "max_risk_per_trade_usd": 5000,
                "max_open_micro_contracts": 20,
                "max_daily_realized_loss_usd": 5000,
                "max_consecutive_losses": 4,
                "max_entries_per_session": 30,
            },
        }
    )


def _plan() -> dict:
    return {
        "schema": "trading_plan_v1",
        "plan_id": EVENT,
        "trigger_event_id": EVENT,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=50)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "config_version": "cfg_gate9_account_loss",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "setup_family": "FAILED_BREAKOUT_REVERSAL",
        "risk_budget_usd": 500,
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "5990.00"},
            "take_profit": {"price": "6010.00"},
        },
    }


def test_equity_beyond_configured_account_loss_limit_fails_closed_even_if_realized_counter_does_not():
    account = AccountState(
        mode="PAPER",
        starting_equity_usd=Decimal("100000"),
        equity_usd=Decimal("94999"),
        daily_realized_pnl_usd=Decimal("0"),
        consecutive_failures=0,
        open_contracts_total=0,
        entries_this_session=0,
    )
    market = MarketSnapshot(
        symbol="MES1!",
        feed_as_of=NOW - timedelta(seconds=5),
        next_bar_start=NOW,
        next_bar_open=Decimal("6000"),
        environment="PROD",
        data_class="REAL",
        source="tradingview",
        healthy=True,
        consecutive_closed_bars=3,
    )
    context = RiskContext(
        now=NOW,
        session_id="CME-2026-09-05",
        session_open=True,
        kill_switch=False,
        account=account,
        market=market,
    )
    plan = _plan()

    decision = ConfigurableRiskGateway(_config()).evaluate(
        plan,
        event_id=EVENT,
        plan_hash=canonical_plan_hash(plan),
        context=context,
    )

    assert decision.approved is False
    assert decision.reason_code == "ACCOUNT_LOSS_LIMIT_REACHED"
    assert decision.intent is None
