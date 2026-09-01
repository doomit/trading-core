from datetime import datetime, timezone

from trading_core.paper_execution import _receipt


NOW = datetime(2026, 9, 1, 3, 20, tzinfo=timezone.utc)
COMMON = {
    "event_id": "evt_receipt_hash_1",
    "plan_id": "evt_receipt_hash_1",
    "status": "PASS",
    "occurred_at": NOW,
    "reason_code": "TEST",
}


def _id(*, plan_hash: str, stage: str, source: str) -> str:
    return _receipt(
        **COMMON,
        plan_hash=plan_hash,
        stage=stage,
        source=source,
    )["receipt_id"]


def test_entry_receipt_identity_binds_canonical_plan_hash_and_is_retry_stable():
    first = _id(plan_hash="a" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")
    retry = _id(plan_hash="a" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")
    conflict = _id(plan_hash="b" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")

    assert first == retry
    assert first != conflict


def test_terminal_exit_receipt_identity_binds_canonical_plan_hash():
    exit_a = _id(plan_hash="a" * 64, stage="PAPER_EXIT_FILLED", source="paper_lifecycle")
    exit_b = _id(plan_hash="b" * 64, stage="PAPER_EXIT_FILLED", source="paper_lifecycle")
    completed_a = _id(plan_hash="a" * 64, stage="COMPLETED", source="azure_executor")
    completed_b = _id(plan_hash="b" * 64, stage="COMPLETED", source="azure_executor")

    assert exit_a != exit_b
    assert completed_a != completed_b
