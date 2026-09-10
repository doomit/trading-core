from __future__ import annotations

from datetime import datetime
from typing import Any


DEFAULT_STALE_AFTER_MINUTES = 15


def market_snapshot_is_stale(
    market: dict[str, Any] | None,
    tick_at: datetime,
    *,
    stale_after_minutes: int = DEFAULT_STALE_AFTER_MINUTES,
) -> bool:
    """Return True when PAPER execution cannot trust the latest observed market time.

    Missing/malformed timestamps fail closed. This helper only decides whether execution
    may consume a snapshot; it never manufactures a price or a fill.
    """
    if tick_at.tzinfo is None:
        raise ValueError("tick_at must include timezone")
    if stale_after_minutes <= 0:
        raise ValueError("stale_after_minutes must be positive")
    if not isinstance(market, dict):
        return True

    latest_end = market.get("latest_bar_end")
    if not latest_end:
        bars = market.get("bars") or []
        if bars and isinstance(bars[-1], dict):
            latest_end = bars[-1].get("end")
    if not latest_end:
        return True

    try:
        observed_at = datetime.fromisoformat(str(latest_end).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if observed_at.tzinfo is None:
        return True

    age_seconds = (tick_at - observed_at).total_seconds()
    return age_seconds >= stale_after_minutes * 60


__all__ = ["DEFAULT_STALE_AFTER_MINUTES", "market_snapshot_is_stale"]
