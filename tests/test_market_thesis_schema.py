import json
from importlib.resources import files

import jsonschema
import pytest


SCHEMA = json.loads(
    files("trading_core.schemas").joinpath("market_thesis_v1.schema.json").read_text()
)


def _thesis() -> dict:
    return {
        "schema": "market_thesis_v1",
        "thesis_id": "deep_ctx_example_MES1_20260901T0903Z",
        "created_at": "2026-09-01T09:03:00Z",
        "valid_until": "2026-09-01T09:17:00Z",
        "symbol": "MES1!",
        "source": "DEEP_SCHEDULER",
        "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
        "strategy_profile": "PA_AGGRESSIVE_A2",
        "regime": {"label": "BEARISH_EXTENSION"},
        "setup_candidates": ["EMA_VWAP_FIRST_PULLBACK"],
        "supporting_evidence": ["price below EMA20 and VWAP"],
        "contrary_evidence": ["price extended"],
        "key_levels": {"EMA20": 7675.5},
        "invalidation": [],
        "watch_conditions": [],
        "confidence": 0.8,
        "paper_only": True,
    }


def test_runtime_config_identity_is_valid_and_required() -> None:
    jsonschema.Draft202012Validator(SCHEMA).validate(_thesis())

    for field in ("config_version", "strategy_profile"):
        thesis = _thesis()
        thesis.pop(field)
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.Draft202012Validator(SCHEMA).validate(thesis)
