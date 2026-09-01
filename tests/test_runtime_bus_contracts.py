import json
from datetime import datetime, timezone
from importlib.resources import files

import pytest
from jsonschema import ValidationError, validate

from trading_core.news_context import validate_news_context


def _schema(name: str) -> dict:
    return json.loads((files("trading_core.schemas") / name).read_text(encoding="utf-8"))


def _plan(**overrides):
    value = {
        "schema": "trading_plan_v1",
        "plan_id": "evt-1",
        "trigger_event_id": "evt-1",
        "created_at": "2026-08-31T06:00:00Z",
        "valid_until": "2026-08-31T06:01:00Z",
        "symbol": "MES1!",
        "decision": "NO_TRADE",
        "confidence": 0.5,
        "analysis_summary": ["test"],
    }
    value.update(overrides)
    return value


def _event(**overrides):
    value = {
        "schema": "trading_event_v1",
        "event_id": "evt-1",
        "type": "MARKET_CONTEXT",
        "timestamp": "2026-08-31T06:00:00Z",
        "symbol": "MES1!",
        "payload": {},
    }
    value.update(overrides)
    return value


def _news(**overrides):
    value = {
        "schema": "market_news_v1",
        "news_id": "provider:item-1",
        "provider": "provider",
        "source_url": "https://example.com/item-1",
        "headline": "Market headline",
        "published_at": "2026-08-31T05:00:00Z",
        "observed_at": "2026-08-31T05:01:00Z",
        "retrieved_at": "2026-08-31T05:01:05Z",
        "affected_symbols": ["MES", "MNQ"],
        "themes": ["rates"],
        "confidence": 0.95,
        "relevance": 0.9,
        "status": "fresh",
        "summary": "Relevant market news.",
    }
    value.update(overrides)
    return value


def _thesis(**overrides):
    value = {
        "schema": "market_thesis_v1",
        "thesis_id": "deep-ctx-1-mes",
        "created_at": "2026-09-01T08:45:00Z",
        "valid_until": "2026-09-01T09:05:00Z",
        "symbol": "MES1!",
        "source": "DEEP_SCHEDULER",
        "based_on_state_version": "ctx-1",
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "regime": {"label": "BEAR_TREND"},
        "setup_candidates": [],
        "supporting_evidence": [],
        "contrary_evidence": [],
        "key_levels": {},
        "invalidation": [],
        "watch_conditions": [],
        "confidence": 0.5,
        "paper_only": True,
    }
    value.update(overrides)
    return value


def test_public_core_owns_plan_event_and_news_json_schemas():
    validate(_plan(), _schema("trading_plan_v1.schema.json"))
    validate(_event(), _schema("trading_event_v1.schema.json"))
    validate(_news(), _schema("market_news_v1.schema.json"))

    with pytest.raises(ValidationError):
        validate(_plan(decision="BUY"), _schema("trading_plan_v1.schema.json"))
    with pytest.raises(ValidationError):
        validate(_news(source_url="not-a-url"), _schema("market_news_v1.schema.json"))


def test_market_thesis_requires_runtime_config_identity():
    validate(_thesis(), _schema("market_thesis_v1.schema.json"))

    with pytest.raises(ValidationError):
        validate(_thesis(config_version=""), _schema("market_thesis_v1.schema.json"))
    with pytest.raises(ValidationError):
        validate(_thesis(strategy_profile=""), _schema("market_thesis_v1.schema.json"))
    without_config = _thesis()
    del without_config["config_version"]
    with pytest.raises(ValidationError):
        validate(without_config, _schema("market_thesis_v1.schema.json"))


def test_news_context_semantics_live_in_core_and_fail_closed():
    assert validate_news_context(_news(), now_iso="2026-08-31T05:02:00Z") == []

    stale = _news(published_at="2026-08-31T02:00:00Z")
    assert any("fresh" in error.lower() for error in validate_news_context(stale, now_iso="2026-08-31T05:02:00Z"))

    future = _news(retrieved_at="2026-08-31T05:03:00Z")
    assert any("future" in error.lower() for error in validate_news_context(future, now_iso="2026-08-31T05:02:00Z"))


def test_news_context_rejects_unexpected_fields_and_bad_scores():
    assert any("unexpected" in error.lower() for error in validate_news_context(_news(extra="x"), now_iso="2026-08-31T05:02:00Z"))
    assert any("confidence" in error for error in validate_news_context(_news(confidence=1.2), now_iso="2026-08-31T05:02:00Z"))
