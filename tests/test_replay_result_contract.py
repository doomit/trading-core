from datetime import datetime, timezone
from decimal import Decimal

import trading_core.replay as replay


def test_replay_result_has_public_machine_readable_validator():
    validator = getattr(replay, "validate_replay_result", None)
    assert callable(validator), "replay_result_v1 needs a public schema validator"

    result = replay.run_replay(
        replay.ReplayConfig(
            symbol="MES",
            dataset_id="mes-fixture-v1",
            timeframe="5m",
            split="DEV",
            strategy_id="fixture-strategy-v1",
            exit_after_bars=1,
            point_value_usd=Decimal("5"),
        ),
        [
            replay.ReplayBar(datetime(2026, 1, 1, 14, 30, tzinfo=timezone.utc), Decimal("6000"), Decimal("6001"), Decimal("5999"), Decimal("6000.5")),
            replay.ReplayBar(datetime(2026, 1, 1, 14, 35, tzinfo=timezone.utc), Decimal("6001"), Decimal("6002"), Decimal("6000"), Decimal("6001.5")),
            replay.ReplayBar(datetime(2026, 1, 1, 14, 40, tzinfo=timezone.utc), Decimal("6002"), Decimal("6003"), Decimal("6001"), Decimal("6002.5")),
        ],
        {0: "LONG"},
    )

    validator(result)
