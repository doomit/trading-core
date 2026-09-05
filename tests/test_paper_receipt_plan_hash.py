from datetime import datetime, timezone

from trading_core.paper_execution import _receipt


NOW = datetime(2026, 9, 5, 20, 30, tzinfo=timezone.utc)
COMMON = {
    "event_id": "evt_gate9_receipt_hash",
    "plan_id": "evt_gate9_receipt_hash",
    "status": "PASS",
    "occurred_at": NOW,
    "reason_code": "TEST",
}


def receipt_id(*, plan_hash: str, stage: str, source: str) -> str:
    return _receipt(
        **COMMON,
        plan_hash=plan_hash,
        stage=stage,
        source=source,
    )["receipt_id"]


def test_receipt_identity_is_retry_stable_but_separates_conflicting_plan_hashes():
    first = receipt_id(plan_hash="a" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")
    retry = receipt_id(plan_hash="a" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")
    conflict = receipt_id(plan_hash="b" * 64, stage="EXECUTOR_RECEIVED", source="azure_executor")

    assert first == retry
    assert first != conflict


def test_terminal_receipts_also_bind_plan_hash():
    exit_a = receipt_id(plan_hash="a" * 64, stage="PAPER_EXIT_FILLED", source="paper_lifecycle")
    exit_b = receipt_id(plan_hash="b" * 64, stage="PAPER_EXIT_FILLED", source="paper_lifecycle")
    completed_a = receipt_id(plan_hash="a" * 64, stage="COMPLETED", source="azure_executor")
    completed_b = receipt_id(plan_hash="b" * 64, stage="COMPLETED", source="azure_executor")

    assert exit_a != exit_b
    assert completed_a != completed_b
