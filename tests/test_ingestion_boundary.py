import pytest


def test_public_ingestion_core_owns_v32_payload_validation():
    from trading_core.ingestion import SERVICE_VERSION, validate_payload

    assert SERVICE_VERSION == "3.2.0"
    payload = {
        "schema": "tv_bars_v2",
        "source": "tradingview",
        "symbol": "MES1!",
        "root": "MES",
        "exchange": "CME_MINI",
        "timeframe": "1",
        "window": 1,
        "sent_at": 1787644260000,
        "bars": [
            {
                "t": 1787644200000,
                "tc": 1787644260000,
                "o": 100.0,
                "h": 101.0,
                "l": 99.0,
                "c": 100.5,
                "v": 10,
            }
        ],
    }

    assert validate_payload(payload) == (None, {})


def test_public_ingestion_core_exposes_repository_independent_processing_api():
    from trading_core import ingestion

    for name in (
        "process_payload",
        "make_ingest_envelope",
        "validate_ingest_envelope",
        "profile_for_live",
        "profile_for_upload",
        "raw_partition",
        "canonical_partition",
        "bar_hash_from_entity",
    ):
        assert callable(getattr(ingestion, name))
