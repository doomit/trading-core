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


def _counts(result: dict) -> dict:
    return result["counts"]


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

    assert _counts(first) == {
        "inserted": 20,
        "duplicates": 0,
        "corrected": 0,
        "conflicts": 0,
        "payload_bars": 20,
    }
    assert _counts(second) == {
        "inserted": 0,
        "duplicates": 20,
        "corrected": 0,
        "conflicts": 0,
        "payload_bars": 20,
    }
    assert len(first["effective_bars"]) == 20
    assert len(second["effective_bars"]) == 20
    assert len(repo.rows) == 20


def test_github_snapshot_uses_authoritative_effective_rows_when_incoming_bar_conflicts():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persist_one_minute_window(
        payload=payload,
        profile=profile,
        repo=repo,
        request_id="req-original",
        received_at=1788800402000,
    )

    conflicting = _payload()
    conflicting["bars"][-1] = dict(conflicting["bars"][-1])
    conflicting["bars"][-1]["c"] = 6520.25
    conflicting["bars"][-1]["h"] = 6521.0
    result = persist_one_minute_window(
        payload=conflicting,
        profile=profile,
        repo=repo,
        request_id="req-conflict",
        received_at=1788800462000,
    )

    assert result["counts"]["conflicts"] == 1
    captured = []
    completed = complete_ingest_after_db(
        payload=conflicting,
        request_id="req-conflict",
        received_at=1788800462000,
        db_committed_at=1788800462090,
        db_result=result["counts"],
        effective_bars=result["effective_bars"],
        mirror_market=captured.append,
        mirror_timestamp=1788800462900,
        logged_at=1788800463000,
    )

    assert completed["accepted"] is True
    assert captured[0]["bars"][-1]["close"] == 6519.5
    assert captured[0]["bars"][-1]["close"] != conflicting["bars"][-1]["c"]


def test_db_success_remains_success_when_github_market_mirror_fails():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persisted = persist_one_minute_window(
        payload=payload,
        profile=profile,
        repo=repo,
        request_id="req-mirror-fail",
        received_at=1788800402000,
    )

    def broken_mirror(_snapshot):
        raise RuntimeError("github unavailable")

    result = complete_ingest_after_db(
        payload=payload,
        request_id="req-mirror-fail",
        received_at=1788800402000,
        db_committed_at=1788800402090,
        db_result=persisted["counts"],
        effective_bars=persisted["effective_bars"],
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
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persisted = persist_one_minute_window(
        payload=payload,
        profile=profile,
        repo=repo,
        request_id="req-ok",
        received_at=1788800402000,
    )
    captured = []

    result = complete_ingest_after_db(
        payload=payload,
        request_id="req-ok",
        received_at=1788800402000,
        db_committed_at=1788800402090,
        db_result={
            **persisted["counts"],
            "inserted": 1,
            "duplicates": 19,
        },
        effective_bars=persisted["effective_bars"],
        mirror_market=captured.append,
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
