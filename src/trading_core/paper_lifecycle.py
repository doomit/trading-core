from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class Bar:
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        for field in ("open", "high", "low", "close"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        if self.low > self.high:
            raise ValueError("bar low cannot exceed high")
        if not self.low <= self.open <= self.high or not self.low <= self.close <= self.high:
            raise ValueError("bar open and close must be within low/high")


@dataclass(frozen=True)
class PaperPosition:
    position_id: str
    symbol: str
    side: str
    quantity: int
    entry_price: Decimal
    stop_price: Decimal
    target_price: Decimal

    def __post_init__(self) -> None:
        if not isinstance(self.position_id, str) or not self.position_id:
            raise ValueError("position_id must be a non-empty string")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if self.side not in {"LONG", "SHORT"}:
            raise ValueError("side must be LONG or SHORT")
        if isinstance(self.quantity, bool) or not isinstance(self.quantity, int) or self.quantity < 1:
            raise ValueError("quantity must be a positive integer")
        for field in ("entry_price", "stop_price", "target_price"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        if self.side == "LONG" and not self.stop_price < self.entry_price < self.target_price:
            raise ValueError("LONG bracket must have stop < entry < target")
        if self.side == "SHORT" and not self.target_price < self.entry_price < self.stop_price:
            raise ValueError("SHORT bracket must have target < entry < stop")


@dataclass(frozen=True)
class BracketResolution:
    position_id: str
    reason_code: str
    exit_price: Decimal | None
    exit_quantity: int
    remaining_quantity: int


def resolve_bracket_bar(position: PaperPosition, bar: Bar) -> BracketResolution:
    """Resolve one closed bar against an OCO stop/target bracket.

    OHLC does not reveal intrabar ordering. If both protective stop and target
    are touched in the same bar, choose the stop deterministically so paper
    results never benefit from unknowable look-ahead ordering. A stop-market
    gap is filled at the worse bar open rather than at an unreachable stop.
    """
    if position.side == "LONG":
        stop_touched = bar.low <= position.stop_price
        target_touched = bar.high >= position.target_price
        stop_gapped = bar.open < position.stop_price
    else:
        stop_touched = bar.high >= position.stop_price
        target_touched = bar.low <= position.target_price
        stop_gapped = bar.open > position.stop_price

    if stop_touched:
        if stop_gapped:
            reason_code = "STOP_FILLED_GAP"
            exit_price = bar.open
        else:
            reason_code = "STOP_FILLED_AMBIGUOUS_BAR" if target_touched else "STOP_FILLED"
            exit_price = position.stop_price
        return BracketResolution(
            position_id=position.position_id,
            reason_code=reason_code,
            exit_price=exit_price,
            exit_quantity=position.quantity,
            remaining_quantity=0,
        )
    if target_touched:
        return BracketResolution(
            position_id=position.position_id,
            reason_code="TARGET_FILLED",
            exit_price=position.target_price,
            exit_quantity=position.quantity,
            remaining_quantity=0,
        )
    return BracketResolution(
        position_id=position.position_id,
        reason_code="POSITION_OPEN",
        exit_price=None,
        exit_quantity=0,
        remaining_quantity=position.quantity,
    )
