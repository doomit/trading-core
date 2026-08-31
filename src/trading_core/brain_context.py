from __future__ import annotations

from typing import Any


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


def build_price_action_context(
    symbol: str,
    bars5: list[dict[str, Any]],
    *,
    recent_limit: int = 24,
) -> dict[str, Any]:
    """Build a bounded no-lookahead snapshot for Brain analysis.

    The caller supplies only bars whose close time is already authoritative. The
    helper derives a few cheap indicators while preserving the raw recent bars
    for LLM price-action interpretation.
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
    }
    tags: list[str] = []
    if ema20 is not None:
        tags.append(
            "ABOVE_EMA20" if close > ema20 else "BELOW_EMA20" if close < ema20 else "AT_EMA20"
        )
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

    return {
        "schema": "price_action_context_v1",
        "symbol": symbol,
        "as_of_5m": int(last.get("tc", int(last["t"]) + 300_000)),
        "features": features,
        "price_action_tags": tags,
        "recent_5m": bars[-recent_limit:],
    }


__all__ = ["build_price_action_context"]
