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


def test_replay_is_byte_stable_and_executes_closed_bar_signal_on_next_bar_open():
    ReplayBar, ReplayConfig, run_replay = _replay_api()
    bars = (
        ReplayBar(datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc), Decimal("6000"), Decimal("6004"), Decimal("5998"), Decimal("6002")),
        ReplayBar(datetime(2026, 8, 31, 14, 35, tzinfo=timezone.utc), Decimal("6003"), Decimal("6008"), Decimal("6001"), Decimal("6006")),
        ReplayBar(datetime(2026, 8, 31, 14, 40, tzinfo=timezone.utc), Decimal("6007"), Decimal("6010"), Decimal("6005"), Decimal("6008")),
    )
    config = ReplayConfig(
        symbol="MES1!",
        dataset_id="synthetic-mes-3bars-v1",
        timeframe="5m",
        split="DEV",
        strategy_id="unit-test-long-after-bar0",
    )

    first = run_replay(config, bars, {0: "LONG"})
    second = run_replay(config, bars, {0: "LONG"})

    first_bytes = json.dumps(first, sort_keys=True, separators=(",", ":")).encode()
    second_bytes = json.dumps(second, sort_keys=True, separators=(",", ":")).encode()
    assert first_bytes == second_bytes
    assert first["fills"] == [
        {
            "signal_bar_index": 0,
            "execution_bar_index": 1,
            "side": "LONG",
            "price": "6003",
        }
    ]
    assert first["dataset_id"] == "synthetic-mes-3bars-v1"
    assert first["split"] == "DEV"
