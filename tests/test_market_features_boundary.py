from datetime import date


def test_public_market_feature_core_owns_session_and_aggregation_logic():
    from trading_core.market_features import (
        RTH,
        aggregate_1m_to_5m,
        expected_1m_timestamps,
        session_phase_for_ms,
    )

    timestamps = expected_1m_timestamps(date(2026, 8, 10))
    assert len(timestamps) == 1380
    rth_open = 1786368600000
    assert session_phase_for_ms(rth_open) == RTH

    bucket = (rth_open // 300_000) * 300_000
    rows = []
    for i in range(5):
        t = bucket + i * 60_000
        rows.append({
            "t": t,
            "tc": t + 60_000,
            "o": 100 + i,
            "h": 101 + i,
            "l": 99 + i,
            "c": 100.5 + i,
            "v": 10 + i,
        })

    bars, partial = aggregate_1m_to_5m(rows)
    assert partial == []
    assert len(bars) == 1
    assert bars[0]["o"] == 100.0
    assert bars[0]["c"] == 104.5
    assert bars[0]["v"] == 60


def test_public_market_feature_core_exposes_deterministic_builder_api():
    from trading_core import market_features

    for name in (
        "EMA",
        "WilderATR",
        "qa_symbol",
        "qa_cross_symbol",
        "build_features",
        "feature_no_lookahead_signature",
        "deterministic_hash",
    ):
        assert hasattr(market_features, name)
