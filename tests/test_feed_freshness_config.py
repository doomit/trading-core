from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_core.configurable_risk import ConfigurableRiskGateway
from trading_core.paper_execution import AccountState, MarketSnapshot, RiskContext, canonical_plan_hash
from trading_core.runtime_config import TradingRuntimeConfig


NOW = datetime(2026, 9, 2, 13, 58, 45, tzinfo=timezone.utc)
EVENT = "evt_feed_freshness_p0"


def config_doc(*, max_feed_age_seconds=None):
    risk = {
        "target_risk_per_trade_usd": 500,
        "max_risk_per_trade_usd": 1000,
        "max_open_micro_contracts": 20,
        "max_daily_realized_loss_usd": 5000,
        "max_consecutive_losses": 4,
        "max_entries_per_session": 30,
    }
    if max_feed_age_seconds is not None:
        risk["max_feed_age_seconds"] = max_feed_age_seconds
    return {
        "schema": "trading_runtime_config_v1",
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "created_at": "2026-09-01T05:00:00+00:00",
        "paper_only": True,
        "account": {"starting_equity_usd": 1_000_000},
        "session": {
            "profile": "CME_INDEX_23H",
            "timezone": "America/Chicago",
            "maintenance_start": "16:00",
            "maintenance_end": "17:00",
        },
        "strategy": {
            "profile": "PA_AGGRESSIVE_A2",
            "analysis_timeframe": "5m",
            "min_action_confidence": 0.60,
            "setup_families": ["STRONG_BODY_CONTINUATION"],
        },
        "risk": risk,
    }


def config(*, max_feed_age_seconds=None):
    return TradingRuntimeConfig.from_document(
        config_doc(max_feed_age_seconds=max_feed_age_seconds)
    )


def account():
    return AccountState(
        mode="PAPER",
        starting_equity_usd=Decimal("1000000"),
        equity_usd=Decimal("1000000"),
        daily_realized_pnl_usd=Decimal("0"),
        consecutive_failures=0,
        open_contracts_total=0,
        entries_this_session=0,
    )


def market(*, age_seconds, source="tradingview", healthy=True):
    return MarketSnapshot(
        symbol="MES1!",
        feed_as_of=NOW - timedelta(seconds=age_seconds),
        next_bar_start=NOW,
        next_bar_open=Decimal("6000.00"),
        environment="PROD",
        data_class="REAL",
        source=source,
        healthy=healthy,
        consecutive_closed_bars=3,
    )


def plan():
    return {
        "schema": "trading_plan_v1",
        "plan_id": EVENT,
        "trigger_event_id": EVENT,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "valid_until": (NOW + timedelta(minutes=5)).isoformat(),
        "symbol": "MES1!",
        "decision": "SHORT",
        "confidence": 0.68,
        "analysis_summary": ["trend continuation"],
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "setup_family": "STRONG_BODY_CONTINUATION",
        "risk_budget_usd": 500,
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "6010.00"},
            "take_profit": {"price": "5980.00"},
        },
    }


def evaluate(*, max_feed_age_seconds, age_seconds, source="tradingview", healthy=True):
    cfg = config(max_feed_age_seconds=max_feed_age_seconds)
    document = plan()
    context = RiskContext(
        now=NOW,
        session_id="CME-2026-09-02",
        session_open=True,
        kill_switch=False,
        account=account(),
        market=market(age_seconds=age_seconds, source=source, healthy=healthy),
    )
    return ConfigurableRiskGateway(cfg).evaluate(
        document,
        event_id=EVENT,
        plan_hash=canonical_plan_hash(document),
        context=context,
    )


def test_legacy_runtime_config_defaults_feed_age_to_90_seconds():
    cfg = config()
    assert cfg.risk.max_feed_age_seconds == 90


def test_explicit_feed_age_limit_round_trips_for_a3_candidate():
    cfg = config(max_feed_age_seconds=180)
    assert cfg.risk.max_feed_age_seconds == 180
    assert cfg.to_document()["risk"]["max_feed_age_seconds"] == 180


@pytest.mark.parametrize("value", [0, -1, 901])
def test_feed_age_limit_is_bounded_inside_compiled_paper_envelope(value):
    with pytest.raises(ValueError, match="max_feed_age_seconds"):
        config(max_feed_age_seconds=value)


def test_trusted_120_second_feed_is_allowed_when_config_limit_is_180():
    decision = evaluate(max_feed_age_seconds=180, age_seconds=120)
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"


def test_feed_past_configured_limit_is_rejected_as_stale_not_untrusted():
    decision = evaluate(max_feed_age_seconds=180, age_seconds=181)
    assert decision.approved is False
    assert decision.reason_code == "STALE_FEED"


def test_bad_provenance_remains_untrusted_even_when_fresh():
    decision = evaluate(max_feed_age_seconds=180, age_seconds=5, source="other")
    assert decision.approved is False
    assert decision.reason_code == "UNTRUSTED_FEED"
