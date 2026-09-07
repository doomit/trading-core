from __future__ import annotations

from trading_core.ingestion import profile_for_live
from trading_core.simple_paper_ingest import (
    complete_ingest_after_db,
    persist_one_minute_window,
)


BASE_T = 1788799200000  # 2026-09-07T16:40:00Z


def _payload() -> dict:
    bars = []
    for i in range(20):
        t = BASE_T + i * 60_000
        px = 6500.0 + i
        bars.append(
            {
                "t": t,
                "tc": t + 60_000,
                "o": px,
                "h": px + 2.0,
                "l": px - 2.0,
                "c": px + 0.5,
                "v": 1000 + i,
            }
        )
    return {
        "schema": "tv_bars_v2",
        "source": "tradingview",
        "symbol": "MES1!",
        "root": "MES",
        "timeframe": "1",
        "window": 20,
        "bars": bars,
    }


class FakeRawRepo:
    def __init__(self):
        self.rows = {}

    def upsert_raw(self, entity, *, correction_mode, request_id, received_at):
        key = (entity["PartitionKey"], entity["RowKey"])
        current = self.rows.get(key)
        if current is None:
            self.rows[key] = dict(entity)
            return {"action": "inserted", "entity": dict(entity)}
        if current["BarHash"] == entity["BarHash"]:
            return {"action": "duplicates", "entity": dict(current)}
        return {"action": "conflicts", "entity": dict(current)}


def test_same_twenty_bar_window_is_idempotent_without_queue_or_canonical_dependency():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)

    first = persist_one_minute_window(
        payload=payload,
        profile=profile,
        repo=repo,
        request_id="req-1",
        received_at=1788800402000,
    )
    second = persist_one_minute_window(
        payload=payload,
        profile=profile,
        repo=repo,
        request_id="req-2",
        received_at=1788800462000,
    )

    assert first == {
        "inserted": 20,
        "duplicates": 0,
        "corrected": 0,
        "conflicts": 0,
        "payload_bars": 20,
    }
    assert second == {
        "inserted": 0,
        "duplicates": 20,
        "corrected": 0,
        "conflicts": 0,
        "payload_bars": 20,
    }
    assert len(repo.rows) == 20


def test_db_success_remains_success_when_github_market_mirror_fails():
    payload = _payload()

    def broken_mirror(_snapshot):
        raise RuntimeError("github unavailable")

    result = complete_ingest_after_db(
        payload=payload,
        request_id="req-mirror-fail",
        received_at=1788800402000,
        db_committed_at=1788800402090,
        db_result={
            "inserted": 1,
            "duplicates": 19,
            "corrected": 0,
            "conflicts": 0,
            "payload_bars": 20,
        },
        mirror_market=broken_mirror,
        mirror_timestamp=1788800402900,
        logged_at=1788800403000,
    )

    assert result["accepted"] is True
    assert result["github_status"] == "FAILED"
    assert result["market_snapshot"] is None
    assert result["ingest_log"]["github_status"] == "FAILED"
    assert result["ingest_log"]["github_mirrored_at"] is None
    assert result["ingest_log"]["github_mirror_latency_ms"] is None
    assert result["ingest_log"]["error"] == "github unavailable"


def test_ingest_snapshot_and_log_expose_exact_market_db_and_github_latency():
    payload = _payload()
    captured = []

    def mirror(snapshot):
        captured.append(snapshot)

    result = complete_ingest_after_db(
        payload=payload,
        request_id="req-ok",
        received_at=1788800402000,
        db_committed_at=1788800402090,
        db_result={
            "inserted": 1,
            "duplicates": 19,
            "corrected": 0,
            "conflicts": 0,
            "payload_bars": 20,
        },
        mirror_market=mirror,
        mirror_timestamp=1788800402900,
        logged_at=1788800403000,
    )

    assert result["accepted"] is True
    assert result["github_status"] == "SUCCESS"
    assert len(captured) == 1
    snapshot = captured[0]
    assert snapshot["schema"] == "market_snapshot_v1"
    assert snapshot["symbol"] == "MES"
    assert snapshot["latest_bar_end"] == "2026-09-07T17:00:00Z"
    assert len(snapshot["bars"]) == 20
    assert snapshot["bars"][-1]["close"] == 6519.5

    log = result["ingest_log"]
    assert log["schema"] == "ingest_log_v1"
    assert log["latest_bar_end"] == "2026-09-07T17:00:00Z"
    assert log["market_to_webhook_latency_ms"] == 2000
    assert log["db_write_latency_ms"] == 90
    assert log["github_mirror_latency_ms"] == 810
    assert log["bars_received"] == 20
    assert log["bars_inserted"] == 1
    assert log["bars_updated"] == 19
    assert log["error"] is None
