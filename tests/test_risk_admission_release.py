from trading_core.risk_admission import (
    ExposureAdmissionRequest,
    ExposureAdmissionState,
    release_exposure,
    reserve_exposure,
)


def test_release_reservation_decrements_capacity_and_advances_revision():
    initial = ExposureAdmissionState(
        account_id="paper-primary",
        session_id="CME-2026-09-03",
        revision=20,
        reserved_contracts_total=0,
    )
    admitted = reserve_exposure(
        initial,
        ExposureAdmissionRequest("plan-a", "event-a", "MES1!", 1),
        max_contracts=1,
    )
    assert admitted.reservation is not None

    released = release_exposure(admitted.next_state, admitted.reservation)

    assert released.approved is True
    assert released.reason_code == "RELEASED"
    assert released.expected_revision == admitted.next_state.revision
    assert released.next_state.revision == admitted.next_state.revision + 1
    assert released.next_state.reserved_contracts_total == 0
    assert released.reservation == admitted.reservation


def test_release_rejects_reservation_for_another_account_or_session():
    initial = ExposureAdmissionState("paper-primary", "CME-2026-09-03", 3, 1)
    foreign = reserve_exposure(
        ExposureAdmissionState("paper-other", "CME-2026-09-04", 9, 0),
        ExposureAdmissionRequest("plan-b", "event-b", "MNQ1!", 1),
        max_contracts=1,
    ).reservation
    assert foreign is not None

    rejected = release_exposure(initial, foreign)

    assert rejected.approved is False
    assert rejected.reason_code == "RESERVATION_SCOPE_MISMATCH"
    assert rejected.expected_revision == initial.revision
    assert rejected.next_state == initial
