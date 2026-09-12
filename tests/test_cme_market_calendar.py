from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from trading_core.cme_market_calendar import (
    canonical_calendar_digest,
    exchange_session_state,
    load_calendar_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SNAPSHOT = ROOT / "config" / "market-calendar" / "cme-equity-index-v1.json"


def _calendar_bytes(*, overrides=None, guard_windows=None) -> bytes:
    payload = {
        "schema": "cme_market_calendar_v1",
        "calendar_id": "cme-equity-index",
        "venue": "CME_GLOBEX",
        "products": ["MES", "MNQ"],
        "timezone": "America/Chicago",
        "generated_at": "2026-09-11T00:00:00Z",
        "effective_from": "2026-01-01T00:00:00Z",
        "effective_until": "2028-12-31T23:59:59Z",
        "source": {
            "authority": "CME Group",
            "trading_hours_url": "https://www.cmegroup.com/trading-hours.html",
            "verified_through_year": 2027,
        },
        "regular_week": {
            "sunday_open": "17:00:00",
            "friday_close": "16:00:00",
            "daily_maintenance_start": "16:00:00",
            "daily_maintenance_end": "17:00:00",
        },
        "overrides": overrides or [],
        "guard_windows": guard_windows or [],
    }
    digest_source = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    payload["content_sha256"] = hashlib.sha256(digest_source).hexdigest()
    return json.dumps(payload, sort_keys=True).encode()


def test_canonical_snapshot_covers_2026_regular_week():
    raw = json.loads(CANONICAL_SNAPSHOT.read_text(encoding="utf-8"))
    assert raw["effective_from"] == "2026-01-01T00:00:00Z"
    assert raw["content_sha256"] == canonical_calendar_digest(raw)
    cal = load_calendar_bytes(CANONICAL_SNAPSHOT.read_bytes())
    assert exchange_session_state(
        "MES", datetime(2026, 9, 9, 20, 0, tzinfo=timezone.utc), cal
    ) == "OPEN"


def test_regular_weekend_is_closed():
    cal = load_calendar_bytes(_calendar_bytes())
    assert exchange_session_state(
        "MNQ", datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc), cal
    ) == "CLOSED"


def test_sunday_1700_chicago_reopen_is_open():
    cal = load_calendar_bytes(_calendar_bytes())
    assert exchange_session_state(
        "MES", datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc), cal
    ) == "OPEN"


def test_daily_maintenance_1600_to_1700_chicago_is_closed():
    cal = load_calendar_bytes(_calendar_bytes())
    # 2026-09-10 is CDT (UTC-5): 16:30 CT == 21:30 UTC.
    assert exchange_session_state(
        "MES", datetime(2026, 9, 10, 21, 30, tzinfo=timezone.utc), cal
    ) == "CLOSED"


def test_unverified_guard_window_beats_regular_open():
    cal = load_calendar_bytes(
        _calendar_bytes(
            guard_windows=[
                {
                    "start": "2028-11-23T00:00:00-06:00",
                    "end": "2028-11-25T00:00:00-06:00",
                    "holiday_name": "Thanksgiving",
                    "verification": "UNVERIFIED",
                    "policy": "FAIL_CLOSED_FOR_NEW_RISK",
                }
            ]
        )
    )
    assert exchange_session_state(
        "MNQ", datetime(2028, 11, 23, 18, 0, tzinfo=timezone.utc), cal
    ) == "UNVERIFIED"


def test_verified_closed_override_beats_regular_open():
    cal = load_calendar_bytes(
        _calendar_bytes(
            overrides=[
                {
                    "start": "2026-12-25T00:00:00-06:00",
                    "end": "2026-12-26T00:00:00-06:00",
                    "status": "CLOSED",
                    "holiday_name": "Christmas",
                    "verification": "VERIFIED",
                    "source_year": 2026,
                }
            ]
        )
    )
    assert exchange_session_state(
        "MES", datetime(2026, 12, 25, 18, 0, tzinfo=timezone.utc), cal
    ) == "CLOSED"


def test_hash_mismatch_is_rejected():
    payload = json.loads(_calendar_bytes())
    payload["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        load_calendar_bytes(json.dumps(payload).encode())


def test_unsupported_symbol_is_rejected():
    cal = load_calendar_bytes(_calendar_bytes())
    with pytest.raises(ValueError, match="unsupported symbol"):
        exchange_session_state("ES", datetime.now(timezone.utc), cal)
