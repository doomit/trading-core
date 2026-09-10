from datetime import datetime, timezone

import pytest

from trading_core.simple_paper_feed_safety import market_snapshot_is_stale


def _market(end: str):
    return {
        "schema": "market_snapshot_v1",
        "symbol": "MES",
        "latest_bar_end": end,
        "bars": [{"end": end, "high": 103.0, "low": 100.0, "close": 101.0}],
    }


def test_market_snapshot_is_stale_at_configured_boundary():
    now = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    assert market_snapshot_is_stale(_market("2026-09-08T01:15:00Z"), now) is True
    assert market_snapshot_is_stale(_market("2026-09-08T01:15:01Z"), now) is False


def test_missing_or_malformed_market_fails_closed():
    now = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    assert market_snapshot_is_stale(None, now) is True
    assert market_snapshot_is_stale({}, now) is True
    assert market_snapshot_is_stale({"latest_bar_end": "not-a-time"}, now) is True


def test_latest_bar_end_fallback_is_supported():
    now = datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc)
    market = {
        "bars": [{"end": "2026-09-08T01:29:00Z", "high": 1.0, "low": 1.0, "close": 1.0}]
    }
    assert market_snapshot_is_stale(market, now) is False


def test_invalid_freshness_inputs_are_rejected():
    with pytest.raises(ValueError, match="tick_at"):
        market_snapshot_is_stale(_market("2026-09-08T01:29:00Z"), datetime(2026, 9, 8, 1, 30))
    with pytest.raises(ValueError, match="positive"):
        market_snapshot_is_stale(
            _market("2026-09-08T01:29:00Z"),
            datetime(2026, 9, 8, 1, 30, tzinfo=timezone.utc),
            stale_after_minutes=0,
        )
