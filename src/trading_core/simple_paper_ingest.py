from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ms(later: str, earlier: str) -> int:
    return max(0, int(round((_dt(later) - _dt(earlier)).total_seconds() * 1000)))


def merge_bar_window(existing: dict[int, dict], bars: list[dict]):
    """Merge a TradingView window by logical bar start time.

    Exact duplicates create no work. A late missing bar inserts history, while a
    changed payload for an existing logical bar is counted as an update.
    """
    merged = {int(k): dict(v) for k, v in existing.items()}
    inserted = 0
    updated = 0
    for raw in bars:
        bar = dict(raw)
        key = int(bar["t"])
        prior = merged.get(key)
        if prior is None:
            merged[key] = bar
            inserted += 1
        elif prior != bar:
            merged[key] = bar
            updated += 1
    return merged, inserted, updated


def build_ingest_log(
    *,
    symbol: str,
    request_id: str,
    latest_bar_end: str,
    received_at: str,
    db_committed_at: str,
    github_status: str,
    github_mirrored_at: str | None,
    bars_received: int,
    bars_inserted: int,
    bars_updated: int,
    error: str | None = None,
    log_id: str | None = None,
) -> dict:
    """Construct the adapter-independent ``ingest_log_v1`` payload.

    DB commit is authoritative. ``github_status=FAILED`` is therefore a valid
    successful-ingest observation and leaves GitHub mirror latency unset.
    """
    if symbol not in {"MES", "MNQ"}:
        raise ValueError("unsupported symbol")
    if github_status not in {"SUCCESS", "FAILED", "SKIPPED"}:
        raise ValueError("invalid github_status")

    return {
        "schema": "ingest_log_v1",
        "log_id": log_id or f"ingest_{uuid4().hex}",
        "symbol": symbol,
        "request_id": request_id,
        "logged_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latest_bar_end": latest_bar_end,
        "received_at": received_at,
        "db_committed_at": db_committed_at,
        "github_status": github_status,
        "github_mirrored_at": github_mirrored_at,
        "bars_received": bars_received,
        "bars_inserted": bars_inserted,
        "bars_updated": bars_updated,
        "market_to_webhook_latency_ms": _ms(received_at, latest_bar_end),
        "db_write_latency_ms": _ms(db_committed_at, received_at),
        "github_mirror_latency_ms": (
            None
            if github_mirrored_at is None
            else _ms(github_mirrored_at, db_committed_at)
        ),
        "error": error,
    }
