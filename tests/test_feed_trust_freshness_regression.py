from datetime import datetime, timedelta, timezone
from decimal import Decimal

from trading_core.configurable_risk import ConfigurableRiskGateway
from trading_core.paper_execution import AccountState, MarketSnapshot, RiskContext, canonical_plan_hash
from trading_core.runtime_config import TradingRuntimeConfig

NOW = datetime(2026, 9, 2, 18, 0, tzinfo=timezone.utc)
EVENT = "evt_feed_trust_regression"


def _config(max_feed_age_seconds: int) -> TradingRuntimeConfig:
    return TradingRuntimeConfig.from_document(
        {
            "schema": "trading_runtime_config_v1",
            "config_version": "cfg_feed_trust_regression",
            "created_at": "2026-09-02T17:00:00+00:00",
            "paper_only": True,
            "account": {"starting_equity_usd": 1_000_000},
            "session": {
                "profile": "CME_INDEX_23H",
                "timezone": "America/Chicago",
                "maintenance_start": "16:00",
                "maintenance_end": "17:00",
            },
            "strategy": {
                "profile": "PA_AGGRESSIVE_A3_TREND",
                "analysis_timeframe": "5m",
                "min_action_confidence": 0.55,
                "setup_families": ["STRONG_BODY_CONTINUATION"],
            },
            "risk": {
                "target_risk_per_trade_usd": 750,
                "max_risk_per_trade_usd": 1500,
                "max_open_micro_contracts": 20,
                "max_daily_realized_loss_usd": 7500,
                "max_consecutive_losses": 5,
                "max_entries_per_session": 40,
                "max_feed_age_seconds": max_feed_age_seconds,
            },
        }
    )


def _context(feed_age_seconds: int) -> RiskContext:
    return RiskContext(
        now=NOW,
        session_id="CME-2026-09-02",
        session_open=True,
        kill_switch=False,
        account=AccountState(
            mode="PAPER",
            starting_equity_usd=Decimal("1000000"),
            equity_usd=Decimal("1000000"),
            daily_realized_pnl_usd=Decimal("0"),
            consecutive_failures=0,
            open_contracts_total=0,
            entries_this_session=0,
        ),
        market=MarketSnapshot(
            symbol="MNQ1!",
            feed_as_of=NOW - timedelta(seconds=feed_age_seconds),
            next_bar_start=NOW,
            next_bar_open=Decimal("24000.00"),
            environment="PROD",
            data_class="REAL",
            source="tradingview",
            healthy=True,
            consecutive_closed_bars=3,
        ),
    )


def _plan() -> dict:
    return {
        "schema": "trading_plan_v1",
        "plan_id": EVENT,
        "trigger_event_id": EVENT,
        "created_at": (NOW - timedelta(seconds=10)).isoformat(),
        "valid_until": (NOW + timedelta(seconds=50)).isoformat(),
        "symbol": "MNQ1!",
        "decision": "SHORT",
        "confidence": 0.70,
        "analysis_summary": ["strong trend continuation"],
        "config_version": "cfg_feed_trust_regression",
        "strategy_profile": "PA_AGGRESSIVE_A3_TREND",
        "setup_family": "STRONG_BODY_CONTINUATION",
        "risk_budget_usd": 750,
        "position_action": {
            "quantity": 1,
            "protective_stop": {"price": "24020.00"},
            "take_profit": {"price": "23960.00"},
        },
    }


def _evaluate(feed_age_seconds: int, max_feed_age_seconds: int):
    document = _plan()
    return ConfigurableRiskGateway(_config(max_feed_age_seconds)).evaluate(
        document,
        event_id=EVENT,
        plan_hash=canonical_plan_hash(document),
        context=_context(feed_age_seconds),
    )


def test_trusted_tradingview_feed_older_than_90_seconds_is_not_untrusted_when_within_configured_age():
    decision = _evaluate(feed_age_seconds=120, max_feed_age_seconds=180)
    assert decision.reason_code != "UNTRUSTED_FEED"
    assert decision.approved is True
    assert decision.reason_code == "RISK_APPROVED"


def test_trusted_feed_beyond_configured_age_fails_closed_as_stale_feed():
    decision = _evaluate(feed_age_seconds=181, max_feed_age_seconds=180)
    assert decision.approved is False
    assert decision.reason_code == "STALE_FEED"
