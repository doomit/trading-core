from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.configurable_risk import ConfigurableRiskGateway
from trading_core.paper_execution import AccountState, MarketSnapshot, RiskContext, canonical_plan_hash
from trading_core.runtime_config import TradingRuntimeConfig


def test_configurable_gateway_counts_reserved_exposure_before_intent():
    now = datetime(2026, 9, 3, 10, 21, tzinfo=timezone.utc)
    config = TradingRuntimeConfig.from_document({
        "schema": "trading_runtime_config_v1",
        "config_version": "cfg_reservation_gate",
        "created_at": "2026-09-03T10:00:00+00:00",
        "paper_only": True,
        "account": {"starting_equity_usd": 1000000},
        "session": {"profile": "CME_INDEX_23H", "timezone": "America/Chicago", "maintenance_start": "16:00", "maintenance_end": "17:00"},
        "strategy": {"profile": "PA_AGGRESSIVE_A2", "analysis_timeframe": "5m", "min_action_confidence": 0.60, "setup_families": ["EMA_VWAP_FIRST_PULLBACK"]},
        "risk": {"target_risk_per_trade_usd": 500, "max_risk_per_trade_usd": 1000, "max_open_micro_contracts": 1, "max_daily_realized_loss_usd": 5000, "max_consecutive_losses": 4, "max_entries_per_session": 30},
    })
    plan = {
        "schema": "trading_plan_v1",
        "plan_id": "evt_cfg_reserved",
        "trigger_event_id": "evt_cfg_reserved",
        "created_at": (now - timedelta(seconds=5)).isoformat(),
        "valid_until": (now + timedelta(seconds=55)).isoformat(),
        "symbol": "MES1!",
        "decision": "LONG",
        "confidence": 0.8,
        "config_version": "cfg_reservation_gate",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "risk_budget_usd": 500,
        "position_action": {"quantity": 1, "protective_stop": {"price": "5990.00"}, "take_profit": {"price": "6010.00"}},
    }
    context = RiskContext(
        now=now,
        session_id="CME-2026-09-03",
        session_open=True,
        kill_switch=False,
        account=AccountState("PAPER", Decimal("1000000"), Decimal("1000000"), Decimal("0"), 0, 0, reserved_contracts_total=1),
        market=MarketSnapshot("MES1!", now - timedelta(seconds=5), now, Decimal("6000.00"), "PROD", "REAL", "tradingview", True, 3),
    )

    decision = ConfigurableRiskGateway(config).evaluate(
        plan,
        event_id=plan["trigger_event_id"],
        plan_hash=canonical_plan_hash(plan),
        context=context,
    )

    assert decision.approved is False
    assert decision.reason_code == "OPEN_ORDER_CONFLICT"
    assert decision.intent is None
