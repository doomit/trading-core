import json
from importlib.resources import files

from jsonschema import Draft202012Validator, FormatChecker


def _validate(schema_name: str, value: dict) -> None:
    schema = json.loads((files("trading_core.schemas") / schema_name).read_text(encoding="utf-8"))
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)


def _flat_ref() -> dict:
    return {"state": "FLAT", "position_id": None, "position_version": None}


def _open_ref(version: int) -> dict:
    return {
        "state": "OPEN",
        "position_id": "MES-20260907-0907-01",
        "position_version": version,
    }


def test_ingest_log_allows_db_success_with_github_mirror_failure():
    _validate(
        "ingest_log_v1.schema.json",
        {
            "schema": "ingest_log_v1",
            "log_id": "ingest-mes-170002",
            "symbol": "MES",
            "request_id": "tv-req-mirror-fail",
            "logged_at": "2026-09-07T17:00:03Z",
            "latest_bar_end": "2026-09-07T17:00:00Z",
            "received_at": "2026-09-07T17:00:02.000Z",
            "db_committed_at": "2026-09-07T17:00:02.090Z",
            "github_status": "FAILED",
            "github_mirrored_at": None,
            "bars_received": 20,
            "bars_inserted": 1,
            "bars_updated": 19,
            "market_to_webhook_latency_ms": 2000,
            "db_write_latency_ms": 90,
            "github_mirror_latency_ms": None,
            "error": "github write failed",
        },
    )


def test_plan_observation_log_allows_read_failure_without_candidate_fields():
    _validate(
        "plan_observation_log_v1.schema.json",
        {
            "schema": "plan_observation_log_v1",
            "log_id": "planobs-mes-170015",
            "symbol": "MES",
            "pulled_at": "2026-09-07T17:00:15Z",
            "candidate_plan_id": None,
            "candidate_generated_at": None,
            "analysis_bar_end": None,
            "current_position": _open_ref(3),
            "outcome": "READ_FAILED",
            "brain_generation_latency_ms": None,
            "plan_pickup_latency_ms": None,
            "error": "github unavailable",
        },
    )


def test_cycle_log_can_record_stale_feed_synthetic_stop_and_flat_result():
    _validate(
        "execution_cycle_log_v1.schema.json",
        {
            "schema": "execution_cycle_log_v1",
            "log_id": "cycle-mes-stale",
            "cycle_id": "cycle-20260907-171500",
            "tick_at": "2026-09-07T17:15:00Z",
            "symbol": "MES",
            "latest_market_end": "2026-09-07T17:00:00Z",
            "market_age_ms": 900000,
            "start_position": _open_ref(3),
            "end_position": _flat_ref(),
            "plan_id": "plan-mes-1000",
            "outcome": "STALE_FEED_FORCED_STOP",
            "synthetic": True,
            "eod_forced": False,
            "stale_feed": True,
            "error": None,
        },
    )
