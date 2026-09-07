from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol
from uuid import uuid4

from .ingestion import raw_entity, validate_payload, validate_profile


class RawWindowRepository(Protocol):
    def upsert_raw(
        self,
        entity: dict,
        *,
        correction_mode: str,
        request_id: str,
        received_at: int,
    ) -> dict: ...


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ms(later: str, earlier: str) -> int:
    return max(0, int(round((_dt(later) - _dt(earlier)).total_seconds() * 1000)))


def _iso(epoch_ms: int) -> str:
    value = datetime.fromtimestamp(int(epoch_ms) / 1000.0, tz=timezone.utc)
    return value.isoformat(timespec="milliseconds").replace(".000+00:00", "Z").replace("+00:00", "Z")


def merge_bar_window(existing: dict[int, dict], bars: list[dict]):
    """Merge a TradingView window by logical bar start time."""
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


def persist_one_minute_window(
    *,
    payload: dict,
    profile: dict,
    repo: RawWindowRepository,
    request_id: str,
    received_at: int,
) -> dict:
    """Persist a TradingView 1m window directly to authoritative RawBars.

    The returned effective rows are the values actually accepted by storage. A
    caller must mirror these rows rather than the incoming payload so an immutable
    conflict cannot make GitHub disagree with Azure.
    """
    error, details = validate_payload(payload)
    if error:
        raise ValueError(f"payload validation failed: {error} {details}")
    error, details = validate_profile(profile)
    if error:
        raise ValueError(f"profile validation failed: {error} {details}")
    if str(payload["timeframe"]) != "1":
        raise ValueError("Simple Paper ingest accepts one-minute bars only")

    counts = {"inserted": 0, "duplicates": 0, "corrected": 0, "conflicts": 0}
    effective_bars: list[dict] = []
    for bar in payload["bars"]:
        incoming = raw_entity(
            payload=payload,
            bar=bar,
            profile=profile,
            request_id=request_id,
            received_at=int(received_at),
        )
        outcome = repo.upsert_raw(
            incoming,
            correction_mode=profile["correction_mode"],
            request_id=request_id,
            received_at=int(received_at),
        )
        action = outcome.get("action")
        if action not in counts:
            raise ValueError(f"unknown raw write action: {action}")
        effective = outcome.get("entity")
        if not isinstance(effective, dict):
            raise ValueError("raw write outcome must include the effective entity")
        counts[action] += 1
        effective_bars.append(dict(effective))

    counts["payload_bars"] = len(payload["bars"])
    effective_bars.sort(key=lambda row: int(row["BarStart"]))
    return {"counts": counts, "effective_bars": effective_bars}


def _market_bar_from_entity(entity: dict) -> dict:
    return {
        "start": _iso(int(entity["BarStart"])),
        "end": _iso(int(entity["BarClose"])),
        "open": float(entity["Open"]),
        "high": float(entity["High"]),
        "low": float(entity["Low"]),
        "close": float(entity["Close"]),
        "volume": float(entity["Volume"]),
    }


def build_market_snapshot(
    *,
    symbol: str,
    effective_bars: list[dict],
    db_committed_at: int,
    github_mirrored_at: int,
) -> dict:
    if symbol not in {"MES", "MNQ"}:
        raise ValueError("Simple Paper market snapshot supports MES/MNQ only")
    if not effective_bars:
        raise ValueError("effective_bars must not be empty")
    rows = sorted(effective_bars, key=lambda row: int(row["BarStart"]))
    bars = [_market_bar_from_entity(row) for row in rows]
    return {
        "schema": "market_snapshot_v1",
        "symbol": symbol,
        "updated_at": _iso(github_mirrored_at),
        "latest_bar_end": bars[-1]["end"],
        "db_committed_at": _iso(db_committed_at),
        "github_mirrored_at": _iso(github_mirrored_at),
        "bars": bars,
    }


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
    logged_at: str | None = None,
) -> dict:
    """Construct the adapter-independent ``ingest_log_v1`` payload."""
    if symbol not in {"MES", "MNQ"}:
        raise ValueError("unsupported symbol")
    if github_status not in {"SUCCESS", "FAILED", "SKIPPED"}:
        raise ValueError("invalid github_status")
    return {
        "schema": "ingest_log_v1",
        "log_id": log_id or f"ingest_{uuid4().hex}",
        "symbol": symbol,
        "request_id": request_id,
        "logged_at": logged_at or datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "latest_bar_end": latest_bar_end,
        "received_at": received_at,
        "db_committed_at": db_committed_at,
        "github_status": github_status,
        "github_mirrored_at": github_mirrored_at,
        "bars_received": int(bars_received),
        "bars_inserted": int(bars_inserted),
        "bars_updated": int(bars_updated),
        "market_to_webhook_latency_ms": _ms(received_at, latest_bar_end),
        "db_write_latency_ms": _ms(db_committed_at, received_at),
        "github_mirror_latency_ms": None if github_mirrored_at is None else _ms(github_mirrored_at, db_committed_at),
        "error": error,
    }


def complete_ingest_after_db(
    *,
    payload: dict,
    request_id: str,
    received_at: int,
    db_committed_at: int,
    db_result: dict,
    effective_bars: list[dict],
    mirror_market: Callable[[dict], None],
    mirror_timestamp: int,
    logged_at: int,
) -> dict:
    """Mirror/log after authoritative DB commit without changing acceptance."""
    symbol = str(payload["root"])
    snapshot = build_market_snapshot(
        symbol=symbol,
        effective_bars=effective_bars,
        db_committed_at=db_committed_at,
        github_mirrored_at=mirror_timestamp,
    )
    github_status = "SUCCESS"
    error = None
    try:
        mirror_market(snapshot)
    except Exception as exc:
        github_status = "FAILED"
        error = str(exc)
        snapshot = None

    effective_mirror_time = mirror_timestamp if github_status == "SUCCESS" else None
    latest_bar_end_ms = max(int(row["BarClose"]) for row in effective_bars)
    ingest_log = build_ingest_log(
        symbol=symbol,
        request_id=request_id,
        latest_bar_end=_iso(latest_bar_end_ms),
        received_at=_iso(received_at),
        db_committed_at=_iso(db_committed_at),
        github_status=github_status,
        github_mirrored_at=_iso(effective_mirror_time) if effective_mirror_time is not None else None,
        bars_received=int(db_result.get("payload_bars", len(effective_bars))),
        bars_inserted=int(db_result.get("inserted", 0)),
        bars_updated=int(db_result.get("duplicates", 0)) + int(db_result.get("corrected", 0)),
        error=error,
        log_id=f"ingest-{symbol.lower()}-{request_id}",
        logged_at=_iso(logged_at),
    )
    return {
        "accepted": True,
        "github_status": github_status,
        "market_snapshot": snapshot,
        "ingest_log": ingest_log,
    }
