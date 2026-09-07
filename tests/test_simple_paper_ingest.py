from __future__ import annotations

from trading_core.ingestion import profile_for_live
from trading_core.simple_paper_ingest import (
    build_ingest_log,
    complete_ingest_after_db,
    merge_bar_window,
    persist_one_minute_window,
)


BASE_T = 1788799200000


def bar(t, c):
    return {"t": t, "tc": t + 60000, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 10}


def _payload(root="MES", symbol="MES1!") -> dict:
    bars = []
    for i in range(20):
        t = BASE_T + i * 60_000
        px = 6500.0 + i
        bars.append({"t": t, "tc": t + 60_000, "o": px, "h": px + 2, "l": px - 2, "c": px + 0.5, "v": 1000 + i})
    return {"schema": "tv_bars_v2", "source": "tradingview", "symbol": symbol, "root": root, "timeframe": "1", "window": 20, "bars": bars}


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


def test_duplicate_windows_are_idempotent_and_late_bar_repairs_history():
    existing = {1000: bar(1000, 1)}
    merged, inserted, updated = merge_bar_window(existing, [bar(1000, 1), bar(2000, 2)])
    assert inserted == 1 and updated == 0 and len(merged) == 2
    merged2, inserted2, updated2 = merge_bar_window(merged, [bar(1000, 1), bar(2000, 2), bar(1500, 1.5)])
    assert inserted2 == 1 and updated2 == 0 and len(merged2) == 3


def test_direct_raw_persistence_is_idempotent_for_replayed_twenty_bar_window():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    first = persist_one_minute_window(payload=payload, profile=profile, repo=repo, request_id="req-1", received_at=1788800402000)
    second = persist_one_minute_window(payload=payload, profile=profile, repo=repo, request_id="req-2", received_at=1788800462000)
    assert first["counts"] == {"inserted": 20, "duplicates": 0, "corrected": 0, "conflicts": 0, "payload_bars": 20}
    assert second["counts"] == {"inserted": 0, "duplicates": 20, "corrected": 0, "conflicts": 0, "payload_bars": 20}
    assert len(repo.rows) == 20


def test_github_snapshot_uses_authoritative_effective_row_not_conflicting_payload():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persist_one_minute_window(payload=payload, profile=profile, repo=repo, request_id="original", received_at=1788800402000)
    conflicting = _payload()
    conflicting["bars"][-1] = {**conflicting["bars"][-1], "c": 6520.25, "h": 6521.0}
    persisted = persist_one_minute_window(payload=conflicting, profile=profile, repo=repo, request_id="conflict", received_at=1788800462000)
    assert persisted["counts"]["conflicts"] == 1
    captured = []
    result = complete_ingest_after_db(payload=conflicting, request_id="conflict", received_at=1788800462000, db_committed_at=1788800462090, db_result=persisted["counts"], effective_bars=persisted["effective_bars"], mirror_market=captured.append, mirror_timestamp=1788800462900, logged_at=1788800463000)
    assert result["accepted"] is True
    assert captured[0]["bars"][-1]["close"] == 6519.5
    assert captured[0]["bars"][-1]["close"] != conflicting["bars"][-1]["c"]


def test_db_success_stays_accepted_when_github_mirror_fails():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persisted = persist_one_minute_window(payload=payload, profile=profile, repo=repo, request_id="mirror-fail", received_at=1788800402000)

    def broken_mirror(_snapshot):
        raise RuntimeError("github unavailable")

    result = complete_ingest_after_db(payload=payload, request_id="mirror-fail", received_at=1788800402000, db_committed_at=1788800402090, db_result=persisted["counts"], effective_bars=persisted["effective_bars"], mirror_market=broken_mirror, mirror_timestamp=1788800402900, logged_at=1788800403000)
    assert result["accepted"] is True
    assert result["github_status"] == "FAILED"
    assert result["market_snapshot"] is None
    assert result["ingest_log"]["github_mirrored_at"] is None
    assert result["ingest_log"]["error"] == "github unavailable"


def test_success_snapshot_and_ingest_log_have_exact_latency_and_window():
    payload = _payload()
    repo = FakeRawRepo()
    profile = profile_for_live(received_at=1788800402000)
    persisted = persist_one_minute_window(payload=payload, profile=profile, repo=repo, request_id="ok", received_at=1788800402000)
    captured = []
    result = complete_ingest_after_db(payload=payload, request_id="ok", received_at=1788800402000, db_committed_at=1788800402090, db_result={**persisted["counts"], "inserted": 1, "duplicates": 19}, effective_bars=persisted["effective_bars"], mirror_market=captured.append, mirror_timestamp=1788800402900, logged_at=1788800403000)
    snapshot = captured[0]
    assert snapshot["schema"] == "market_snapshot_v1"
    assert snapshot["symbol"] == "MES"
    assert snapshot["latest_bar_end"] == "2026-09-07T17:00:00Z"
    assert len(snapshot["bars"]) == 20
    log = result["ingest_log"]
    assert log["market_to_webhook_latency_ms"] == 2000
    assert log["db_write_latency_ms"] == 90
    assert log["github_mirror_latency_ms"] == 810
    assert log["bars_received"] == 20
    assert log["bars_inserted"] == 1
    assert log["bars_updated"] == 19


def test_existing_string_log_builder_still_represents_github_failure():
    log = build_ingest_log(symbol="MES", request_id="r1", latest_bar_end="2026-09-07T17:40:00Z", received_at="2026-09-07T17:40:02Z", db_committed_at="2026-09-07T17:40:02.250Z", github_status="FAILED", github_mirrored_at=None, bars_received=20, bars_inserted=2, bars_updated=1, error="mirror failed")
    assert log["github_status"] == "FAILED"
    assert log["market_to_webhook_latency_ms"] == 2000
    assert log["db_write_latency_ms"] == 250
    assert log["github_mirror_latency_ms"] is None
