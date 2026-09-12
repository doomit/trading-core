from __future__ import annotations

from datetime import datetime
from typing import Any


_TRADING = "TRADING"
_BLOCKED_STATES = {
    "CLOSE_ONLY",
    "MARKET_CLOSED",
    "CALENDAR_UNVERIFIED",
    "DATA_STALE",
    "WARMING_UP",
}


def effective_market_state(
    *,
    exchange_state: str,
    close_only: bool,
    feed_stale: bool,
    warmup_count: int,
) -> str:
    """Combine exchange, strategy, feed, and warm-up state using fail-closed priority."""
    if exchange_state == "UNVERIFIED":
        return "CALENDAR_UNVERIFIED"
    if exchange_state == "CLOSED":
        return "MARKET_CLOSED"
    if exchange_state != "OPEN":
        return "CALENDAR_UNVERIFIED"
    if close_only:
        return "CLOSE_ONLY"
    if feed_stale:
        return "DATA_STALE"
    if warmup_count < 3:
        return "WARMING_UP"
    return _TRADING


def can_increase_exposure(state: str) -> bool:
    """Return True only when the effective state explicitly permits new risk."""
    return state == _TRADING


def update_warmup(
    previous: dict[str, Any] | None,
    latest_bar_end: str | None,
    exchange_state: str,
) -> dict[str, Any]:
    """Track a capped sequence of consecutive advancing one-minute bars after reopen.

    Duplicate observations are expected because execution runs faster than the one-minute
    feed: a duplicate neither increments nor resets the sequence. A gap or out-of-order
    timestamp breaks the prior sequence and fails closed until three new consecutive bars
    have been observed again.
    """
    if exchange_state != "OPEN":
        return {"count": 0, "last_bar_end": None}

    prior = previous or {"count": 0, "last_bar_end": None}
    prior_count = max(0, int(prior.get("count", 0)))
    prior_end = prior.get("last_bar_end")

    if not latest_bar_end:
        return {"count": prior_count, "last_bar_end": prior_end}

    current = _parse_timestamp(latest_bar_end)
    if not prior_end:
        return {"count": 1, "last_bar_end": latest_bar_end}

    prior_time = _parse_timestamp(str(prior_end))
    delta = (current - prior_time).total_seconds()
    if delta == 0:
        return {"count": prior_count, "last_bar_end": prior_end}
    if delta == 60:
        return {"count": min(3, prior_count + 1), "last_bar_end": latest_bar_end}
    if delta > 0:
        return {"count": 1, "last_bar_end": latest_bar_end}
    return {"count": 0, "last_bar_end": latest_bar_end}


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("bar timestamp must include timezone")
    return parsed


__all__ = ["can_increase_exposure", "effective_market_state", "update_warmup"]
