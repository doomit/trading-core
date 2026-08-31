from trading_core.brain_context import build_price_action_context


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
