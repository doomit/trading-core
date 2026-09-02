import pytest
from jsonschema import ValidationError

from trading_core.runtime_activity import load_runtime_activity_schema, validate_runtime_activity


def activity(**overrides):
    receipt = {
        "schema": "runtime_activity_v1",
        "receipt_id": "azure-event-evt-1-created",
        "event_id": "evt-1",
        "stage": "EVENT_CREATED",
        "status": "PASS",
        "occurred_at": "2026-08-31T00:30:00Z",
        "source": "azure_event_producer",
        "symbol": "MES",
        "details": {"state_version": "market-42"},
    }
    receipt.update(overrides)
    return receipt


def test_runtime_activity_schema_is_packaged_and_accepts_valid_receipt():
    schema = load_runtime_activity_schema()
    assert schema["$id"] == "runtime_activity_v1.schema.json"
    validate_runtime_activity(activity())


def test_runtime_activity_rejects_unknown_stage_and_secret_detail_key():
    with pytest.raises(ValidationError):
        validate_runtime_activity(activity(stage="MAGIC_STAGE"))
    with pytest.raises(ValidationError):
        validate_runtime_activity(activity(details={"Authorization": "not-allowed"}))


def test_plan_stages_require_plan_id():
    with pytest.raises(ValidationError):
        validate_runtime_activity(activity(stage="PLAN_VALIDATED", source="github_validation"))
    validate_runtime_activity(
        activity(
            receipt_id="plan-evt-1-validated",
            stage="PLAN_VALIDATED",
            source="github_validation",
            plan_id="plan-evt-1",
        )
    )


def test_paper_exit_receipt_matches_the_public_runtime_activity_contract():
    validate_runtime_activity(
        activity(
            receipt_id="paper-exit-evt-1",
            stage="PAPER_EXIT_FILLED",
            source="paper_lifecycle",
            plan_id="evt-1",
            details={"decision": "LONG"},
        )
    )


def test_occurred_at_must_be_timezone_aware_iso_timestamp():
    with pytest.raises((ValidationError, ValueError)):
        validate_runtime_activity(activity(occurred_at="2026-08-31T00:30:00"))
