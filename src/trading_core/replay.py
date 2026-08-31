from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Mapping, Sequence


_ALLOWED_SPLITS = {"DEV", "VALID", "OOS"}
_ALLOWED_SIDES = {"LONG", "SHORT"}


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

    def __post_init__(self) -> None:
        if not all(isinstance(value, str) and value for value in (self.symbol, self.dataset_id, self.timeframe, self.strategy_id)):
            raise ValueError("replay config identifiers must be non-empty strings")
        if self.split not in _ALLOWED_SPLITS:
            raise ValueError("split must be DEV, VALID, or OOS")
        if self.fee_per_fill_usd < 0 or self.slippage_points < 0:
            raise ValueError("replay cost assumptions must be non-negative")
        for boundary in (self.start, self.end):
            if boundary is not None and (boundary.tzinfo is None or boundary.utcoffset() is None):
                raise ValueError("replay split boundaries must be timezone-aware")
        if self.start is not None and self.end is not None and self.start > self.end:
            raise ValueError("replay split start must not be after end")


def _in_split(config: ReplayConfig, bar: ReplayBar) -> bool:
    return (config.start is None or bar.start >= config.start) and (config.end is None or bar.start <= config.end)


def run_replay(
    config: ReplayConfig,
    bars: Sequence[ReplayBar],
    signals_by_bar_index: Mapping[int, str],
) -> dict[str, object]:
    """Run deterministic closed-bar replay without executing across an explicit split boundary."""
    if any(later.start <= earlier.start for earlier, later in zip(bars, bars[1:])):
        raise ValueError("replay bars must be strictly ordered")

    fills: list[dict[str, object]] = []
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
        raw_open = bars[execution_bar_index].open
        fill_price = raw_open + config.slippage_points if side == "LONG" else raw_open - config.slippage_points
        fills.append({"signal_bar_index": signal_bar_index, "execution_bar_index": execution_bar_index, "side": side, "price": str(fill_price)})

    total_fees = config.fee_per_fill_usd * len(fills)
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
        "total_fees_usd": str(total_fees),
        "fills": fills,
    }


__all__ = ["ReplayBar", "ReplayConfig", "run_replay"]
