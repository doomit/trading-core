from __future__ import annotations

from trading_core.market_session_gate import (
    can_increase_exposure,
    effective_market_state,
    update_warmup,
)


def test_calendar_unverified_has_highest_priority():
    assert effective_market_state(
        exchange_state="UNVERIFIED",
        close_only=False,
        feed_stale=False,
        warmup_count=3,
    ) == "CALENDAR_UNVERIFIED"


def test_calendar_closed_beats_fresh_bar():
    assert effective_market_state(
        exchange_state="CLOSED",
        close_only=False,
        feed_stale=False,
        warmup_count=3,
    ) == "MARKET_CLOSED"


def test_close_only_beats_feed_and_warmup():
    assert effective_market_state(
        exchange_state="OPEN",
        close_only=True,
        feed_stale=False,
        warmup_count=3,
    ) == "CLOSE_ONLY"


def test_expected_open_stale_feed_is_data_stale_not_closed():
    assert effective_market_state(
        exchange_state="OPEN",
        close_only=False,
        feed_stale=True,
        warmup_count=3,
    ) == "DATA_STALE"


def test_reopen_requires_three_advancing_one_minute_bars():
    state = update_warmup(None, "2026-09-13T22:01:00Z", "OPEN")
    assert state == {"count": 1, "last_bar_end": "2026-09-13T22:01:00Z"}
    state = update_warmup(state, "2026-09-13T22:02:00Z", "OPEN")
    assert state["count"] == 2
    state = update_warmup(state, "2026-09-13T22:03:00Z", "OPEN")
    assert state["count"] == 3
    assert effective_market_state(
        exchange_state="OPEN",
        close_only=False,
        feed_stale=False,
        warmup_count=state["count"],
    ) == "TRADING"


def test_duplicate_bar_neither_advances_nor_resets_warmup():
    state = {"count": 2, "last_bar_end": "2026-09-13T22:02:00Z"}
    assert update_warmup(state, "2026-09-13T22:02:00Z", "OPEN") == state


def test_gap_starts_a_new_consecutive_sequence():
    state = {"count": 2, "last_bar_end": "2026-09-13T22:02:00Z"}
    next_state = update_warmup(state, "2026-09-13T22:05:00Z", "OPEN")
    assert next_state == {"count": 1, "last_bar_end": "2026-09-13T22:05:00Z"}


def test_closed_exchange_resets_warmup():
    state = {"count": 3, "last_bar_end": "2026-09-11T20:59:00Z"}
    assert update_warmup(state, "2026-09-11T20:59:00Z", "CLOSED") == {
        "count": 0,
        "last_bar_end": None,
    }


def test_only_trading_state_may_increase_exposure():
    assert can_increase_exposure("TRADING") is True
    for blocked in (
        "CLOSE_ONLY",
        "MARKET_CLOSED",
        "CALENDAR_UNVERIFIED",
        "DATA_STALE",
        "WARMING_UP",
    ):
        assert can_increase_exposure(blocked) is False
