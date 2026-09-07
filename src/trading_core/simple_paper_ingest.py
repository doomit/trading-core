from __future__ import annotations

from datetime import datetime, timezone
from typing import Callable, Protocol

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


def _iso(epoch_ms: int) -> str:
    value = datetime.fromtimestamp(int(epoch_ms) / 1000.0, tz=timezone.utc)
    return value.isoformat(timespec="milliseconds").replace(".000+00:00", "Z").replace("+00:00", "Z")


def persist_one_minute_window(
    *,
    payload: dict,
    profile: dict,
    repo: RawWindowRepository,
    request_id: str,
    received_at: int,
) -> dict:
    """Persist one TradingView one-minute window directly to authoritative raw storage.

    Returns both write counts and the effective persisted entities chosen by the
    repository. The latter is important for immutable/conflict semantics: callers
    must mirror what the DB accepted, never a conflicting incoming value.

    This intentionally performs no queue publication, Canonical5m build, BAR_READY
    publication, status projection, or downstream orchestration.
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
    """Build the Brain-facing snapshot only from authoritative effective rows."""
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
    effective_bars: list[dict],
    request_id: str,
    received_at: int,
    db_committed_at: int,
    db_result: dict,
    github_status: str,
    github_mirrored_at: int | None,
    logged_at: int,
    error: str | None,
) -> dict:
    if not effective_bars:
        raise ValueError("effective_bars must not be empty")
    latest_bar_end_ms = max(int(row["BarClose"]) for row in effective_bars)
    github_latency = None
    if github_mirrored_at is not None:
        github_latency = max(0, int(github_mirrored_at) - int(db_committed_at))

    return {
        "schema": "ingest_log_v1",
        "log_id": f"ingest-{symbol.lower()}-{request_id}",
        "symbol": symbol,
        "request_id": request_id,
        "logged_at": _iso(logged_at),
        "latest_bar_end": _iso(latest_bar_end_ms),
        "received_at": _iso(received_at),
        "db_committed_at": _iso(db_committed_at),
        "github_status": github_status,
        "github_mirrored_at": _iso(github_mirrored_at) if github_mirrored_at is not None else None,
        "bars_received": int(db_result.get("payload_bars", len(effective_bars))),
        "bars_inserted": int(db_result.get("inserted", 0)),
        # v1 contract has no explicit duplicate count. `bars_updated` means rows
        # already present/effectively retained plus corrections, so the dashboard
        # can still reconcile bars_received = inserted + updated + conflicts.
        "bars_updated": int(db_result.get("duplicates", 0)) + int(db_result.get("corrected", 0)),
        "market_to_webhook_latency_ms": max(0, int(received_at) - latest_bar_end_ms),
        "db_write_latency_ms": max(0, int(db_committed_at) - int(received_at)),
        "github_mirror_latency_ms": github_latency,
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
    """Best-effort mirror/log phase after authoritative DB commit.

    Once this function is called the caller has already durably committed the bar
    window. Therefore mirror failure is captured as data and never changes the
    returned ingest acceptance result.
    """
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
    except Exception as exc:  # mirror is explicitly non-authoritative
        github_status = "FAILED"
        error = str(exc)
        snapshot = None

    effective_mirror_time = mirror_timestamp if github_status == "SUCCESS" else None
    ingest_log = build_ingest_log(
        symbol=symbol,
        effective_bars=effective_bars,
        request_id=request_id,
        received_at=received_at,
        db_committed_at=db_committed_at,
        db_result=db_result,
        github_status=github_status,
        github_mirrored_at=effective_mirror_time,
        logged_at=logged_at,
        error=error,
    )
    return {
        "accepted": True,
        "github_status": github_status,
        "market_snapshot": snapshot,
        "ingest_log": ingest_log,
    }
