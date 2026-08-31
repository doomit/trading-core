import json
from datetime import datetime, timezone
from decimal import Decimal

import pytest


def _replay_api():
    try:
        from trading_core.replay import ReplayBar, ReplayConfig, run_replay
    except ModuleNotFoundError:
        pytest.fail("trading_core.replay deterministic replay contract is not implemented")
    return ReplayBar, ReplayConfig, run_replay


def _bars(ReplayBar):
    return (
        ReplayBar(datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc), Decimal("6000"), Decimal("6004"), Decimal("5998"), Decimal("6002")),
        ReplayBar(datetime(2026, 8, 31, 14, 35, tzinfo=timezone.utc), Decimal("6003"), Decimal("6008"), Decimal("6001"), Decimal("6006")),
        ReplayBar(datetime(2026, 8, 31, 14, 40, tzinfo=timezone.utc), Decimal("6007"), Decimal("6010"), Decimal("6005"), Decimal("6008")),
    )


def _six_bars(ReplayBar):
    return tuple(
        ReplayBar(
            datetime(2026, 8, 31, 14, 30 + 5 * index, tzinfo=timezone.utc),
            Decimal(6000 + index),
            Decimal(6002 + index),
            Decimal(5998 + index),
            Decimal(6001 + index),
        )
        for index in range(6)
    )


def test_replay_is_byte_stable_and_executes_closed_bar_signal_on_next_bar_open():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(symbol="MES1!", dataset_id="synthetic-mes-3bars-v1", timeframe="5m", split="DEV", strategy_id="unit-test-long-after-bar0")
    first = run_replay(config, _bars(ReplayBar), {0: "LONG"})
    second = run_replay(config, _bars(ReplayBar), {0: "LONG"})
    assert json.dumps(first, sort_keys=True, separators=(",", ":")).encode() == json.dumps(second, sort_keys=True, separators=(",", ":")).encode()
    assert first["fills"] == [{"signal_bar_index": 0, "execution_bar_index": 1, "side": "LONG", "role": "ENTRY", "price": "6003"}]
    assert first["dataset_id"] == "synthetic-mes-3bars-v1"
    assert first["split"] == "DEV"


def test_replay_applies_explicit_fee_and_adverse_slippage_assumptions():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(
        symbol="MES1!", dataset_id="synthetic-mes-costs-v1", timeframe="5m", split="DEV", strategy_id="unit-test-costs",
        fee_per_fill_usd=Decimal("1.30"), slippage_points=Decimal("0.25"),
    )
    result = run_replay(config, _bars(ReplayBar), {0: "LONG"})
    assert result["fills"] == [{"signal_bar_index": 0, "execution_bar_index": 1, "side": "LONG", "role": "ENTRY", "price": "6003.25"}]
    assert result["fee_per_fill_usd"] == "1.30"
    assert result["slippage_points"] == "0.25"
    assert result["total_fees_usd"] == "1.30"


def test_replay_does_not_fill_a_signal_when_next_bar_is_outside_split_boundary():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    split_start = datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc)
    split_end = datetime(2026, 8, 31, 14, 35, tzinfo=timezone.utc)
    config = ReplayConfig(
        symbol="MES1!", dataset_id="synthetic-mes-split-v1", timeframe="5m", split="DEV", strategy_id="unit-test-split",
        start=split_start, end=split_end,
    )

    result = run_replay(config, _bars(ReplayBar), {1: "LONG"})

    assert result["fills"] == []
    assert result["bar_count"] == 2
    assert result["start"] == split_start.isoformat()
    assert result["end"] == split_end.isoformat()


def test_replay_emits_round_trip_trade_ledger_and_net_metrics_with_explicit_point_value():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(
        symbol="MES1!",
        dataset_id="synthetic-mes-roundtrip-v1",
        timeframe="5m",
        split="DEV",
        strategy_id="unit-test-roundtrip",
        fee_per_fill_usd=Decimal("1.30"),
        slippage_points=Decimal("0.25"),
        exit_after_bars=1,
        point_value_usd=Decimal("5"),
    )

    result = run_replay(config, _bars(ReplayBar), {0: "LONG"})

    assert result["fills"] == [
        {"signal_bar_index": 0, "execution_bar_index": 1, "side": "LONG", "role": "ENTRY", "price": "6003.25"},
        {"signal_bar_index": 0, "execution_bar_index": 2, "side": "SHORT", "role": "EXIT", "price": "6006.75"},
    ]
    assert result["trades"] == [
        {
            "signal_bar_index": 0,
            "entry_bar_index": 1,
            "exit_bar_index": 2,
            "side": "LONG",
            "entry_price": "6003.25",
            "exit_price": "6006.75",
            "gross_pnl_usd": "17.50",
            "fees_usd": "2.60",
            "net_pnl_usd": "14.90",
        }
    ]
    assert result["metrics"] == {
        "trade_count": 1,
        "winner_count": 1,
        "loser_count": 0,
        "gross_pnl_usd": "17.50",
        "net_pnl_usd": "14.90",
    }
    assert result["total_fees_usd"] == "2.60"


def test_replay_skips_entry_when_configured_round_trip_cannot_exit_inside_split():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    split_end = datetime(2026, 8, 31, 14, 35, tzinfo=timezone.utc)
    config = ReplayConfig(
        symbol="MES1!",
        dataset_id="synthetic-mes-roundtrip-split-v1",
        timeframe="5m",
        split="DEV",
        strategy_id="unit-test-roundtrip-split",
        fee_per_fill_usd=Decimal("1.30"),
        exit_after_bars=1,
        point_value_usd=Decimal("5"),
        end=split_end,
    )

    result = run_replay(config, _bars(ReplayBar), {0: "LONG"})

    assert result["fills"] == []
    assert result["trades"] == []
    assert result["total_fees_usd"] == "0.00"
    assert result["metrics"]["net_pnl_usd"] == "0.00"


def test_replay_skips_signal_whose_entry_would_overlap_an_active_round_trip():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(
        symbol="MES1!",
        dataset_id="synthetic-mes-one-position-v1",
        timeframe="5m",
        split="DEV",
        strategy_id="unit-test-one-position",
        exit_after_bars=2,
        point_value_usd=Decimal("5"),
    )

    result = run_replay(config, _six_bars(ReplayBar), {0: "LONG", 1: "SHORT"})

    assert [(trade["signal_bar_index"], trade["entry_bar_index"], trade["exit_bar_index"]) for trade in result["trades"]] == [(0, 1, 3)]
    assert [(fill["signal_bar_index"], fill["role"]) for fill in result["fills"]] == [(0, "ENTRY"), (0, "EXIT")]
    assert result["metrics"]["trade_count"] == 1


def test_replay_does_not_reenter_on_same_bar_as_prior_exit_but_allows_later_entry():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(
        symbol="MNQ1!",
        dataset_id="synthetic-mnq-reentry-v1",
        timeframe="5m",
        split="VALID",
        strategy_id="unit-test-reentry-boundary",
        exit_after_bars=1,
        point_value_usd=Decimal("2"),
    )

    result = run_replay(config, _six_bars(ReplayBar), {0: "LONG", 1: "SHORT", 2: "SHORT"})

    assert [(trade["signal_bar_index"], trade["entry_bar_index"], trade["exit_bar_index"]) for trade in result["trades"]] == [
        (0, 1, 2),
        (2, 3, 4),
    ]
    assert [(fill["signal_bar_index"], fill["role"]) for fill in result["fills"]] == [
        (0, "ENTRY"), (0, "EXIT"), (2, "ENTRY"), (2, "EXIT")
    ]


def test_replay_config_rejects_bool_for_exit_after_bars():
    _, ReplayConfig, _ = _replay_api()

    with pytest.raises(ValueError, match="exit_after_bars must be a positive integer"):
        ReplayConfig(
            symbol="MES1!",
            dataset_id="synthetic-mes-invalid-exit-bars-v1",
            timeframe="5m",
            split="DEV",
            strategy_id="unit-test-invalid-exit-bars",
            exit_after_bars=True,
        )


def test_replay_result_embeds_canonical_config_and_stable_config_identity():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    config = ReplayConfig(
        symbol="MES1!",
        dataset_id="synthetic-mes-roundtrip-v1",
        timeframe="5m",
        split="DEV",
        strategy_id="unit-test-roundtrip",
        fee_per_fill_usd=Decimal("1.30"),
        slippage_points=Decimal("0.25"),
        exit_after_bars=1,
        point_value_usd=Decimal("5"),
    )

    result = run_replay(config, _bars(ReplayBar), {0: "LONG"})

    assert result["config"] == {
        "symbol": "MES1!",
        "dataset_id": "synthetic-mes-roundtrip-v1",
        "timeframe": "5m",
        "split": "DEV",
        "strategy_id": "unit-test-roundtrip",
        "fee_per_fill_usd": "1.30",
        "slippage_points": "0.25",
        "start": None,
        "end": None,
        "exit_after_bars": 1,
        "point_value_usd": "5",
    }
    assert result["config_id"] == "025bf2c3f35fb76361e3c168d9a26f8a941d83646fd13ea882a9a16fb8f2cc12"
