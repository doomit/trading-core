from datetime import datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest

from trading_core.runtime_config import TradingRuntimeConfig, cme_session_state

CT = ZoneInfo("America/Chicago")


def config_doc(**overrides):
    doc = {
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
            "setup_families": [
                "EMA_VWAP_FIRST_PULLBACK",
                "H1_L1_TREND_CONTINUATION",
                "H2_L2_TREND_CONTINUATION",
                "FAILED_BREAKOUT_REVERSAL",
                "STRONG_BODY_CONTINUATION",
                "RANGE_EDGE_SCALP",
            ],
        },
        "risk": {
            "target_risk_per_trade_usd": 500,
            "max_risk_per_trade_usd": 1000,
            "max_open_micro_contracts": 20,
            "max_daily_realized_loss_usd": 5000,
            "max_consecutive_losses": 4,
            "max_entries_per_session": 30,
        },
    }
    doc.update(overrides)
    return doc


def test_initial_runtime_config_parses_to_typed_policy():
    cfg = TradingRuntimeConfig.from_document(config_doc())
    assert cfg.config_version == "cfg_pa_aggressive_a2_1m_20260901_001"
    assert cfg.paper_only is True
    assert cfg.account.starting_equity_usd == Decimal("1000000")
    assert cfg.strategy.profile == "PA_AGGRESSIVE_A2"
    assert cfg.strategy.min_action_confidence == Decimal("0.60")
    assert cfg.risk.target_risk_per_trade_usd == Decimal("500")
    assert cfg.risk.max_risk_per_trade_usd == Decimal("1000")
    assert cfg.risk.max_open_micro_contracts == 20
    assert cfg.risk.max_daily_realized_loss_usd == Decimal("5000")
    assert cfg.risk.max_consecutive_losses == 4
    assert cfg.risk.max_entries_per_session == 30


def test_strategy_profile_is_config_data_not_a_compiled_whitelist():
    doc = config_doc()
    doc["strategy"] = dict(doc["strategy"], profile="PA_RESEARCH_A3")
    cfg = TradingRuntimeConfig.from_document(doc)
    assert cfg.strategy.profile == "PA_RESEARCH_A3"


@pytest.mark.parametrize(
    "mutate",
    [
        lambda d: d.__setitem__("paper_only", False),
        lambda d: d["account"].__setitem__("starting_equity_usd", 10_000_001),
        lambda d: d["risk"].__setitem__("max_risk_per_trade_usd", 25_001),
        lambda d: d["risk"].__setitem__("max_open_micro_contracts", 101),
        lambda d: d["risk"].__setitem__("max_daily_realized_loss_usd", 100_001),
        lambda d: d["risk"].__setitem__("max_consecutive_losses", 21),
        lambda d: d["risk"].__setitem__("max_entries_per_session", 201),
    ],
)
def test_runtime_config_rejects_values_outside_compiled_paper_envelopes(mutate):
    doc = config_doc()
    mutate(doc)
    with pytest.raises(ValueError):
        TradingRuntimeConfig.from_document(doc)


def test_runtime_config_rejects_target_risk_above_hard_max():
    doc = config_doc()
    doc["risk"]["target_risk_per_trade_usd"] = 1001
    with pytest.raises(ValueError, match="target_risk"):
        TradingRuntimeConfig.from_document(doc)


@pytest.mark.parametrize(
    "field,value",
    [
        ("profile", "OTHER_SESSION"),
        ("timezone", "UTC"),
        ("maintenance_start", "15:59"),
        ("maintenance_end", "17:01"),
    ],
)
def test_cme_session_exchange_safety_fields_are_not_tunable(field, value):
    doc = config_doc()
    doc["session"][field] = value
    with pytest.raises(ValueError):
        TradingRuntimeConfig.from_document(doc)


def test_runtime_config_round_trip_preserves_policy_values():
    cfg = TradingRuntimeConfig.from_document(config_doc())
    round_trip = TradingRuntimeConfig.from_document(cfg.to_document())
    assert round_trip == cfg


@pytest.mark.parametrize(
    "local_iso,expected_open,expected_id",
    [
        ("2026-08-30T16:59:00-05:00", False, "CME-2026-08-30"),  # Sunday before weekly open
        ("2026-08-30T17:00:00-05:00", True, "CME-2026-08-31"),   # Sunday open -> Monday trade date
        ("2026-08-31T15:59:00-05:00", True, "CME-2026-08-31"),
        ("2026-08-31T16:00:00-05:00", False, "CME-2026-08-31"),
        ("2026-08-31T16:59:00-05:00", False, "CME-2026-08-31"),
        ("2026-08-31T17:00:00-05:00", True, "CME-2026-09-01"),
        ("2026-09-04T15:59:00-05:00", True, "CME-2026-09-04"),   # Friday
        ("2026-09-04T16:00:00-05:00", False, "CME-2026-09-04"),
        ("2026-09-04T17:00:00-05:00", False, "CME-2026-09-05"), # weekend remains closed
        ("2026-09-05T12:00:00-05:00", False, "CME-2026-09-05"), # Saturday
    ],
)
def test_cme_index_23h_session_boundaries(local_iso, expected_open, expected_id):
    cfg = TradingRuntimeConfig.from_document(config_doc())
    now = datetime.fromisoformat(local_iso).astimezone(CT)
    state = cme_session_state(now, cfg)
    assert state.session_open is expected_open
    assert state.session_id == expected_id
