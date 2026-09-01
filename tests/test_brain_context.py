from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from trading_core.brain_context import build_price_action_context

CT = ZoneInfo("America/Chicago")


def _ms(year, month, day, hour, minute):
    return int(datetime(year, month, day, hour, minute, tzinfo=CT).astimezone(timezone.utc).timestamp() * 1000)


def test_context_derives_ema_atr_and_recent_bars_without_future_data():
    bars = []
    start = 1_700_000_000_000
    for i in range(30):
        open_price = 100 + i * 0.25
        close = open_price + 0.2
        bars.append(
            {
                "t": start + i * 300_000,
                "tc": start + (i + 1) * 300_000,
                "o": open_price,
                "h": close + 0.3,
                "l": open_price - 0.2,
                "c": close,
                "v": 100 + i,
            }
        )
    context = build_price_action_context("MES1!", bars, recent_limit=12)
    assert context["schema"] == "price_action_context_v1"
    assert context["symbol"] == "MES1!"
    assert len(context["recent_5m"]) == 12
    assert context["features"]["EMA20"] is not None
    assert context["features"]["ATR14"] is not None
    assert context["as_of_5m"] == bars[-1]["tc"]
    assert context["features"]["Close"] == bars[-1]["c"]


def test_context_marks_inside_bar_and_strong_body_tags():
    bars = [
        {"t": 0, "tc": 300_000, "o": 100, "h": 102, "l": 99, "c": 101, "v": 100},
        {"t": 300_000, "tc": 600_000, "o": 100.1, "h": 101.5, "l": 99.5, "c": 101.4, "v": 100},
    ]
    context = build_price_action_context("MES1!", bars, recent_limit=12)
    assert "INSIDE_BAR" in context["price_action_tags"]


def test_context_exposes_rth_opening_ranges_session_vwap_and_prior_session_levels():
    bars = []
    for i, (o, h, l, c, v) in enumerate(
        [(100, 102, 99, 101, 10), (101, 103, 100, 102, 20), (102, 104, 101, 103, 30)]
    ):
        t = _ms(2026, 8, 28, 8, 30 + i * 5)
        bars.append({"t": t, "tc": t + 300_000, "o": o, "h": h, "l": l, "c": c, "v": v})

    current = [
        (110, 111, 109, 110.5, 10),
        (110.5, 112, 110, 111.5, 20),
        (111.5, 113, 111, 112.5, 30),
        (112.5, 114, 112, 113.5, 40),
        (113.5, 115, 113, 114.5, 50),
        (114.5, 116, 114, 115.5, 60),
    ]
    for i, (o, h, l, c, v) in enumerate(current):
        t = _ms(2026, 8, 31, 8, 30 + i * 5)
        bars.append({"t": t, "tc": t + 300_000, "o": o, "h": h, "l": l, "c": c, "v": v})

    context = build_price_action_context("MES1!", bars, recent_limit=4)
    f = context["features"]
    assert f["OR5High"] == 111
    assert f["OR5Low"] == 109
    assert f["OR15High"] == 113
    assert f["OR15Low"] == 109
    assert f["OR30High"] == 116
    assert f["OR30Low"] == 109
    assert f["SessionHigh"] == 116
    assert f["SessionLow"] == 109
    assert f["PriorSessionHigh"] == 104
    assert f["PriorSessionLow"] == 99
    assert f["VWAP"] is not None
    assert len(context["recent_5m"]) == 4
