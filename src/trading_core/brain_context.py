from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def _ema(values: list[float], length: int = 20) -> float | None:
    if len(values) < length:
        return None
    value = sum(values[:length]) / length
    alpha = 2.0 / (length + 1.0)
    for item in values[length:]:
        value = alpha * item + (1.0 - alpha) * value
    return value


def _atr(bars: list[dict[str, Any]], length: int = 14) -> float | None:
    if len(bars) < length:
        return None
    true_ranges: list[float] = []
    previous_close: float | None = None
    for bar in bars:
        high = float(bar["h"])
        low = float(bar["l"])
        close = float(bar["c"])
        true_ranges.append(
            high - low
            if previous_close is None
            else max(high - low, abs(high - previous_close), abs(low - previous_close))
        )
        previous_close = close
    value = sum(true_ranges[:length]) / length
    for item in true_ranges[length:]:
        value = ((length - 1) * value + item) / length
    return value


def _session_date(epoch_ms: int):
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).astimezone(CT)
    session_date = dt.date()
    if dt.hour >= 17:
        from datetime import timedelta
        session_date += timedelta(days=1)
    return session_date


def _is_rth(epoch_ms: int) -> bool:
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).astimezone(CT)
    minute = dt.hour * 60 + dt.minute
    return 8 * 60 + 30 <= minute < 15 * 60


def _rth_minute(epoch_ms: int) -> int:
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc).astimezone(CT)
    return dt.hour * 60 + dt.minute - (8 * 60 + 30)


def _opening_range(rth: list[dict[str, Any]], minutes: int) -> tuple[float | None, float | None]:
    selected = [bar for bar in rth if 0 <= _rth_minute(int(bar["t"])) < minutes]
    expected = minutes // 5
    if len(selected) < expected:
        return None, None
    selected = sorted(selected, key=lambda bar: int(bar["t"]))[:expected]
    if [_rth_minute(int(bar["t"])) for bar in selected] != list(range(0, minutes, 5)):
        return None, None
    return max(float(bar["h"]) for bar in selected), min(float(bar["l"]) for bar in selected)


def _session_features(bars: list[dict[str, Any]]) -> dict[str, Any]:
    by_session: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for bar in bars:
        by_session[_session_date(int(bar["t"]))].append(bar)
    current_date = _session_date(int(bars[-1]["t"]))
    current = sorted(by_session[current_date], key=lambda bar: int(bar["t"]))
    prior_dates = sorted(date for date in by_session if date < current_date)
    prior = sorted(by_session[prior_dates[-1]], key=lambda bar: int(bar["t"])) if prior_dates else []
    rth = [bar for bar in current if _is_rth(int(bar["t"]))]

    volume = sum(max(0, int(bar["v"])) for bar in current)
    pv = sum(
        ((float(bar["h"]) + float(bar["l"]) + float(bar["c"])) / 3.0)
        * max(0, int(bar["v"]))
        for bar in current
    )
    or5h, or5l = _opening_range(rth, 5)
    or15h, or15l = _opening_range(rth, 15)
    or30h, or30l = _opening_range(rth, 30)
    return {
        "VWAP": None if volume <= 0 else pv / volume,
        "SessionHigh": max(float(bar["h"]) for bar in current),
        "SessionLow": min(float(bar["l"]) for bar in current),
        "PriorSessionHigh": max(float(bar["h"]) for bar in prior) if prior else None,
        "PriorSessionLow": min(float(bar["l"]) for bar in prior) if prior else None,
        "PriorSessionClose": float(prior[-1]["c"]) if prior else None,
        "OR5High": or5h,
        "OR5Low": or5l,
        "OR15High": or15h,
        "OR15Low": or15l,
        "OR30High": or30h,
        "OR30Low": or30l,
    }


def build_price_action_context(
    symbol: str,
    bars5: list[dict[str, Any]],
    *,
    recent_limit: int = 24,
) -> dict[str, Any]:
    """Build a bounded no-lookahead snapshot for Brain analysis.

    `bars5` may contain a wider calculation window (for EMA/ATR/session levels),
    while only `recent_limit` raw bars are returned to the caller. Every input
    bar must already be closed/authoritative.
    """
    if not isinstance(symbol, str) or not symbol:
        raise ValueError("symbol is required")
    if recent_limit <= 0:
        raise ValueError("recent_limit must be positive")
    bars = sorted(bars5, key=lambda bar: int(bar["t"]))
    if not bars:
        raise ValueError("bars5 is required")

    last = bars[-1]
    previous = bars[-2] if len(bars) > 1 else None
    open_price = float(last["o"])
    high = float(last["h"])
    low = float(last["l"])
    close = float(last["c"])
    bar_range = high - low
    body = abs(close - open_price)
    ema20 = _ema([float(bar["c"]) for bar in bars], 20)
    atr14 = _atr(bars, 14)

    features: dict[str, Any] = {
        "Open": open_price,
        "High": high,
        "Low": low,
        "Close": close,
        "Volume": int(last["v"]),
        "EMA20": ema20,
        "ATR14": atr14,
        "BodyPctOfRange": None if bar_range <= 0 else body / bar_range,
        "CloseLocation": None if bar_range <= 0 else (close - low) / bar_range,
        **_session_features(bars),
    }
    if atr14 and atr14 > 0 and ema20 is not None:
        features["CloseVsEMA20ATR"] = (close - ema20) / atr14
    else:
        features["CloseVsEMA20ATR"] = None
    if atr14 and atr14 > 0 and features["VWAP"] is not None:
        features["CloseVsVWAPATR"] = (close - float(features["VWAP"])) / atr14
    else:
        features["CloseVsVWAPATR"] = None

    tags: list[str] = []
    if ema20 is not None:
        tags.append(
            "ABOVE_EMA20" if close > ema20 else "BELOW_EMA20" if close < ema20 else "AT_EMA20"
        )
    vwap = features["VWAP"]
    if isinstance(vwap, (int, float)):
        tags.append("ABOVE_VWAP" if close > vwap else "BELOW_VWAP" if close < vwap else "AT_VWAP")
    if previous is not None and high < float(previous["h"]) and low > float(previous["l"]):
        tags.append("INSIDE_BAR")
    if (
        features["BodyPctOfRange"] is not None
        and features["BodyPctOfRange"] >= 0.65
        and features["CloseLocation"] >= 0.75
    ):
        tags.append("STRONG_BULL_BODY")
    elif (
        features["BodyPctOfRange"] is not None
        and features["BodyPctOfRange"] >= 0.65
        and features["CloseLocation"] <= 0.25
    ):
        tags.append("STRONG_BEAR_BODY")

    or15_high = features["OR15High"]
    or15_low = features["OR15Low"]
    previous_close = float(previous["c"]) if previous is not None else None
    if isinstance(or15_high, (int, float)) and previous_close is not None and previous_close <= or15_high < close:
        tags.append("OR_BREAKOUT_UP")
    if isinstance(or15_low, (int, float)) and previous_close is not None and previous_close >= or15_low > close:
        tags.append("OR_BREAKOUT_DOWN")

    return {
        "schema": "price_action_context_v1",
        "symbol": symbol,
        "as_of_5m": int(last.get("tc", int(last["t"]) + 300_000)),
        "features": features,
        "price_action_tags": tags,
        "recent_5m": bars[-recent_limit:],
    }


__all__ = ["build_price_action_context"]
