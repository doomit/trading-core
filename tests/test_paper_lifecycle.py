from decimal import Decimal

from trading_core.paper_lifecycle import Bar, PaperPosition, resolve_bracket_bar


def test_same_bar_stop_and_target_collision_fails_closed_to_stop():
    position = PaperPosition(
        position_id="pos-1",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        entry_price=Decimal("6000.00"),
        stop_price=Decimal("5995.00"),
        target_price=Decimal("6005.00"),
    )
    bar = Bar(
        open=Decimal("6000.00"),
        high=Decimal("6006.00"),
        low=Decimal("5994.00"),
        close=Decimal("6001.00"),
    )

    result = resolve_bracket_bar(position, bar)

    assert result.reason_code == "STOP_FILLED_AMBIGUOUS_BAR"
    assert result.exit_price == Decimal("5995.00")
    assert result.exit_quantity == 1
    assert result.remaining_quantity == 0


def test_target_only_bar_closes_at_target():
    position = PaperPosition(
        position_id="pos-2",
        symbol="MNQ1!",
        side="SHORT",
        quantity=1,
        entry_price=Decimal("21000.00"),
        stop_price=Decimal("21020.00"),
        target_price=Decimal("20960.00"),
    )
    bar = Bar(
        open=Decimal("20995.00"),
        high=Decimal("21005.00"),
        low=Decimal("20950.00"),
        close=Decimal("20970.00"),
    )

    result = resolve_bracket_bar(position, bar)

    assert result.reason_code == "TARGET_FILLED"
    assert result.exit_price == Decimal("20960.00")
    assert result.remaining_quantity == 0


def test_bar_touching_neither_leg_keeps_position_open():
    position = PaperPosition(
        position_id="pos-3",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        entry_price=Decimal("6000.00"),
        stop_price=Decimal("5995.00"),
        target_price=Decimal("6005.00"),
    )
    bar = Bar(
        open=Decimal("6000.00"),
        high=Decimal("6004.00"),
        low=Decimal("5996.00"),
        close=Decimal("6002.00"),
    )

    result = resolve_bracket_bar(position, bar)

    assert result.reason_code == "POSITION_OPEN"
    assert result.exit_price is None
    assert result.exit_quantity == 0
    assert result.remaining_quantity == 1


def test_long_gap_through_stop_fills_at_worse_bar_open():
    position = PaperPosition(
        position_id="pos-4",
        symbol="MES1!",
        side="LONG",
        quantity=1,
        entry_price=Decimal("6000.00"),
        stop_price=Decimal("5995.00"),
        target_price=Decimal("6005.00"),
    )
    bar = Bar(
        open=Decimal("5990.00"),
        high=Decimal("5994.00"),
        low=Decimal("5988.00"),
        close=Decimal("5992.00"),
    )

    result = resolve_bracket_bar(position, bar)

    assert result.reason_code == "STOP_FILLED_GAP"
    assert result.exit_price == Decimal("5990.00")


def test_short_gap_through_stop_fills_at_worse_bar_open():
    position = PaperPosition(
        position_id="pos-5",
        symbol="MNQ1!",
        side="SHORT",
        quantity=1,
        entry_price=Decimal("21000.00"),
        stop_price=Decimal("21020.00"),
        target_price=Decimal("20960.00"),
    )
    bar = Bar(
        open=Decimal("21030.00"),
        high=Decimal("21035.00"),
        low=Decimal("21025.00"),
        close=Decimal("21028.00"),
    )

    result = resolve_bracket_bar(position, bar)

    assert result.reason_code == "STOP_FILLED_GAP"
    assert result.exit_price == Decimal("21030.00")
