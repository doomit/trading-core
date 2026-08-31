from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence


_ALLOWED_SPLITS = {"DEV", "VALID", "OOS"}
_ALLOWED_SIDES = {"LONG", "SHORT"}
_USD_QUANTUM = Decimal("0.01")


@dataclass(frozen=True)
class ReplayBar:
    start: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal

    def __post_init__(self) -> None:
        if self.start.tzinfo is None or self.start.utcoffset() is None:
            raise ValueError("replay bar start must be timezone-aware")
        if self.high < max(self.open, self.close) or self.low > min(self.open, self.close) or self.low > self.high:
            raise ValueError("invalid OHLC bar")


@dataclass(frozen=True)
class ReplayConfig:
    symbol: str
    dataset_id: str
    timeframe: str
    split: str
    strategy_id: str
    fee_per_fill_usd: Decimal = Decimal("0")
    slippage_points: Decimal = Decimal("0")
    start: datetime | None = None
    end: datetime | None = None
    exit_after_bars: int | None = None
    point_value_usd: Decimal = Decimal("1")

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.symbol, self.dataset_id, self.timeframe, self.strategy_id)):
            raise ValueError("replay config identifiers must be non-empty strings")
        if self.split not in _ALLOWED_SPLITS:
            raise ValueError("split must be DEV, VALID, or OOS")
        if self.fee_per_fill_usd < 0 or self.slippage_points < 0:
            raise ValueError("replay cost assumptions must be non-negative")
        if self.exit_after_bars is not None and (
            isinstance(self.exit_after_bars, bool)
            or not isinstance(self.exit_after_bars, int)
            or self.exit_after_bars < 1
        ):
            raise ValueError("exit_after_bars must be a positive integer")
        if self.point_value_usd <= 0:
            raise ValueError("point_value_usd must be positive")
        for boundary in (self.start, self.end):
            if boundary is not None and (boundary.tzinfo is None or boundary.utcoffset() is None):
                raise ValueError("replay split boundaries must be timezone-aware")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("replay split start must not be after end")


def _in_split(config: ReplayConfig, bar: ReplayBar) -> bool:
    return (config.start is None or bar.start >= config.start) and (config.end is None or bar.start <= config.end)


def _format_usd(value: Decimal) -> str:
    return format(value.quantize(_USD_QUANTUM), "f")


def run_replay(
    config: ReplayConfig,
    bars: Sequence[ReplayBar],
    signals_by_bar_index: Mapping[int, str],
) -> dict[str, object]:
    """Run deterministic closed-bar replay without executing across an explicit split boundary."""
    if any(later.start <= earlier.start for earlier, later in zip(bars, bars[1:])):
        raise ValueError("replay bars must be strictly ordered")

    fills: list[dict[str, object]] = []
    trades: list[dict[str, object]] = []
    fill_count = 0
    active_exit_bar_index: int | None = None
    for signal_bar_index in sorted(signals_by_bar_index):
        if not isinstance(signal_bar_index, int) or signal_bar_index < 0 or signal_bar_index >= len(bars):
            raise ValueError("signal bar index is outside the replay dataset")
        side = signals_by_bar_index[signal_bar_index]
        if side not in _ALLOWED_SIDES:
            raise ValueError("signal side must be LONG or SHORT")
        execution_bar_index = signal_bar_index + 1
        if execution_bar_index >= len(bars):
            continue
        if not _in_split(config, bars[signal_bar_index]) or not _in_split(config, bars[execution_bar_index]):
            continue

        exit_bar_index: int | None = None
        if config.exit_after_bars is not None:
            if active_exit_bar_index is not None and execution_bar_index <= active_exit_bar_index:
                continue
            exit_bar_index = execution_bar_index + config.exit_after_bars
            if exit_bar_index >= len(bars) or not _in_split(config, bars[exit_bar_index]):
                continue

        raw_open = bars[execution_bar_index].open
        fill_price = raw_open + config.slippage_points if side == "LONG" else raw_open - config.slippage_points
        fills.append(
            {
                "signal_bar_index": signal_bar_index,
                "execution_bar_index": execution_bar_index,
                "side": side,
                "role": "ENTRY",
                "price": str(fill_price),
            }
        )
        fill_count += 1

        if exit_bar_index is None:
            continue
        raw_exit = bars[exit_bar_index].open
        exit_price = raw_exit - config.slippage_points if side == "LONG" else raw_exit + config.slippage_points
        exit_side = "SHORT" if side == "LONG" else "LONG"
        fills.append(
            {
                "signal_bar_index": signal_bar_index,
                "execution_bar_index": exit_bar_index,
                "side": exit_side,
                "role": "EXIT",
                "price": str(exit_price),
            }
        )
        gross_points = exit_price - fill_price if side == "LONG" else fill_price - exit_price
        gross_pnl = gross_points * config.point_value_usd
        fees = config.fee_per_fill_usd * 2
        net_pnl = gross_pnl - fees
        fill_count += 1
        trades.append(
            {
                "signal_bar_index": signal_bar_index,
                "entry_bar_index": execution_bar_index,
                "exit_bar_index": exit_bar_index,
                "side": side,
                "entry_price": str(fill_price),
                "exit_price": str(exit_price),
                "gross_pnl_usd": _format_usd(gross_pnl),
                "fees_usd": _format_usd(fees),
                "net_pnl_usd": _format_usd(net_pnl),
            }
        )
        active_exit_bar_index = exit_bar_index

    total_fees = config.fee_per_fill_usd * fill_count
    gross_total = sum((Decimal(trade["gross_pnl_usd"]) for trade in trades), Decimal("0"))
    net_total = sum((Decimal(trade["net_pnl_usd"]) for trade in trades), Decimal("0"))
    winner_count = sum(1 for trade in trades if Decimal(trade["net_pnl_usd"]) > 0)
    loser_count = sum(1 for trade in trades if Decimal(trade["net_pnl_usd"]) < 0)
    return {
        "schema": "replay_result_v1",
        "symbol": config.symbol,
        "dataset_id": config.dataset_id,
        "timeframe": config.timeframe,
        "split": config.split,
        "strategy_id": config.strategy_id,
        "start": config.start.isoformat() if config.start is not None else None,
        "end": config.end.isoformat() if config.end is not None else None,
        "bar_count": sum(1 for bar in bars if _in_split(config, bar)),
        "fee_per_fill_usd": str(config.fee_per_fill_usd),
        "slippage_points": str(config.slippage_points),
        "point_value_usd": str(config.point_value_usd),
        "total_fees_usd": str(total_fees),
        "fills": fills,
        "trades": trades,
        "metrics": {
            "trade_count": len(trades),
            "winner_count": winner_count,
            "loser_count": loser_count,
            "gross_pnl_usd": _format_usd(gross_total),
            "net_pnl_usd": _format_usd(net_total),
        },
    }


__all__ = ["ReplayBar", "ReplayConfig", "run_replay"]
