from datetime import datetime, timezone
from decimal import Decimal

from trading_core.replay import ReplayBar, ReplayConfig, run_replay


def test_replay_config_identity_is_namespaced_by_explicit_schema_version():
    bars = (
        ReplayBar(datetime(2026, 8, 31, 14, 30, tzinfo=timezone.utc), Decimal("6000"), Decimal("6004"), Decimal("5998"), Decimal("6002")),
        ReplayBar(datetime(2026, 8, 31, 14, 35, tzinfo=timezone.utc), Decimal("6003"), Decimal("6008"), Decimal("6001"), Decimal("6006")),
        ReplayBar(datetime(2026, 8, 31, 14, 40, tzinfo=timezone.utc), Decimal("6007"), Decimal("6010"), Decimal("6005"), Decimal("6008")),
    )
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

    result = run_replay(config, bars, {0: "LONG"})

    assert result["config"]["schema"] == "replay_config_v1"
    assert result["config_id"] == "282587f6124d14ffa5285e2569a0c2b0b2615851b3ca690c8383e6a490259d63"
