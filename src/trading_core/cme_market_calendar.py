from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, time
from importlib.resources import files
from typing import Any
from zoneinfo import ZoneInfo

from jsonschema import Draft202012Validator


_SCHEMA_FILE = "cme_market_calendar_v1.schema.json"
_OPEN = "OPEN"
_CLOSED = "CLOSED"
_UNVERIFIED = "UNVERIFIED"


def canonical_calendar_digest(calendar: dict[str, Any]) -> str:
    """Return the SHA-256 of the canonical payload with content_sha256 omitted."""
    payload = dict(calendar)
    payload.pop("content_sha256", None)
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def load_calendar_bytes(payload: bytes) -> dict[str, Any]:
    """Parse, schema-validate, and digest-validate one CME calendar snapshot."""
    try:
        calendar = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("calendar payload must be valid UTF-8 JSON") from exc
    if not isinstance(calendar, dict):
        raise ValueError("calendar payload must be a JSON object")

    schema_text = files("trading_core.schemas").joinpath(_SCHEMA_FILE).read_text(encoding="utf-8")
    schema = json.loads(schema_text)
    Draft202012Validator(schema).validate(calendar)

    expected = str(calendar["content_sha256"])
    actual = canonical_calendar_digest(calendar)
    if not hmac.compare_digest(expected, actual):
        raise ValueError("calendar sha256 mismatch")

    start = _parse_timestamp(calendar["effective_from"])
    end = _parse_timestamp(calendar["effective_until"])
    if start >= end:
        raise ValueError("calendar effective_from must be earlier than effective_until")
    ZoneInfo(calendar["timezone"])
    return calendar


def exchange_session_state(
    symbol: str,
    now: datetime,
    calendar: dict[str, Any],
) -> str:
    """Return OPEN, CLOSED, or UNVERIFIED for the expected CME exchange state."""
    if now.tzinfo is None:
        raise ValueError("now must include timezone")
    products = calendar.get("products") or []
    if symbol not in products:
        raise ValueError(f"unsupported symbol: {symbol!r}")

    effective_from = _parse_timestamp(calendar["effective_from"])
    effective_until = _parse_timestamp(calendar["effective_until"])
    if now < effective_from or now > effective_until:
        return _UNVERIFIED

    for guard in calendar.get("guard_windows") or []:
        if _within(now, guard["start"], guard["end"]):
            return _UNVERIFIED

    for override in calendar.get("overrides") or []:
        if _within(now, override["start"], override["end"]):
            status = override["status"]
            if status == "OPEN":
                return _OPEN
            return _CLOSED

    local = now.astimezone(ZoneInfo(calendar["timezone"]))
    local_time = local.timetz().replace(tzinfo=None)
    rules = calendar["regular_week"]
    sunday_open = time.fromisoformat(rules["sunday_open"])
    friday_close = time.fromisoformat(rules["friday_close"])
    maintenance_start = time.fromisoformat(rules["daily_maintenance_start"])
    maintenance_end = time.fromisoformat(rules["daily_maintenance_end"])

    weekday = local.weekday()  # Monday=0 ... Sunday=6
    if weekday == 5:
        return _CLOSED
    if weekday == 6:
        return _OPEN if local_time >= sunday_open else _CLOSED
    if weekday == 4:
        return _OPEN if local_time < friday_close else _CLOSED
    if maintenance_start <= local_time < maintenance_end:
        return _CLOSED
    return _OPEN


def _within(now: datetime, start: str, end: str) -> bool:
    return _parse_timestamp(start) <= now < _parse_timestamp(end)


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("calendar timestamp must include timezone")
    return parsed


__all__ = [
    "canonical_calendar_digest",
    "exchange_session_state",
    "load_calendar_bytes",
]
