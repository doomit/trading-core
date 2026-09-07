from trading_core.simple_paper_ingest import build_ingest_log, merge_bar_window


def bar(t, c):
    return {"t": t, "tc": t + 60000, "o": c, "h": c + 1, "l": c - 1, "c": c, "v": 10}


def test_duplicate_windows_are_idempotent_and_late_bar_repairs_history():
    existing = {1000: bar(1000, 1)}
    merged, inserted, updated = merge_bar_window(existing, [bar(1000, 1), bar(2000, 2)])
    assert inserted == 1 and updated == 0 and len(merged) == 2

    merged2, inserted2, updated2 = merge_bar_window(
        merged, [bar(1000, 1), bar(2000, 2), bar(1500, 1.5)]
    )
    assert inserted2 == 1 and updated2 == 0 and len(merged2) == 3


def test_changed_existing_bar_counts_as_update_not_duplicate_work():
    existing = {1000: bar(1000, 1)}
    changed = bar(1000, 1.25)
    merged, inserted, updated = merge_bar_window(existing, [changed])
    assert inserted == 0 and updated == 1 and merged[1000]["c"] == 1.25


def test_db_success_github_failure_is_successful_ingest_log_with_latencies():
    log = build_ingest_log(
        symbol="MES",
        request_id="r1",
        latest_bar_end="2026-09-07T17:40:00Z",
        received_at="2026-09-07T17:40:02Z",
        db_committed_at="2026-09-07T17:40:02.250Z",
        github_status="FAILED",
        github_mirrored_at=None,
        bars_received=20,
        bars_inserted=2,
        bars_updated=1,
        error="mirror failed",
    )
    assert log["github_status"] == "FAILED"
    assert log["market_to_webhook_latency_ms"] == 2000
    assert log["db_write_latency_ms"] == 250
    assert log["github_mirror_latency_ms"] is None
