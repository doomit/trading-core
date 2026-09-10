from __future__ import annotations

from datetime import datetime, time
from typing import Any
from zoneinfo import ZoneInfo

from .simple_paper_position_execution import _bar_end, _close_transition, _latest_bar


EOD_TIMEZONE = "America/Los_Angeles"
EOD_LOCAL_TIME = time(23, 55)


def eod_close_due(
    tick_at: datetime,
    *,
    timezone_name: str = EOD_TIMEZONE,
    close_time: time = EOD_LOCAL_TIME,
) -> bool:
    """Return whether the PAPER v1 23:55 local EOD boundary has been reached."""
    if tick_at.tzinfo is None:
        raise ValueError("tick_at must include timezone")
    local = tick_at.astimezone(ZoneInfo(timezone_name))
    return local.timetz().replace(tzinfo=None) >= close_time


def force_eod_close(
    *,
    current_position: dict[str, Any],
    market: dict[str, Any] | None,
    executed_at: datetime,
    cycle_id: str,
    account: dict[str, Any],
    point_value: float,
) -> dict[str, Any] | None:
    """Close one OPEN PAPER position at the latest available close once EOD is due."""
    if current_position.get("status") != "OPEN" or not eod_close_due(executed_at):
        return None
    bar = _latest_bar(market)
    if market is None or bar is None:
        return None
    market_end = _bar_end(market, bar)
    return _close_transition(
        position=current_position,
        account=account,
        cycle_id=cycle_id,
        plan_id=current_position.get("active_plan_id"),
        action="EOD_FORCED_CLOSE",
        outcome="EOD_FORCED_CLOSE",
        price=float(bar["close"]),
        market_bar_end=market_end,
        executed_at=executed_at,
        point_value=point_value,
        synthetic=False,
        reason="EOD_FORCED_CLOSE",
    )
