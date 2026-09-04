from trading_core.risk_admission import (
    ExposureAdmissionRequest,
    ExposureAdmissionState,
    ExposureReservation,
    release_exposure,
    reserve_exposure,
)


def test_reservation_mutation_carries_revision_for_single_winner_storage_cas():
    state = ExposureAdmissionState(
        account_id="paper-primary",
        session_id="CME-2026-09-03",
        revision=7,
        reserved_contracts_total=0,
    )
    request = ExposureAdmissionRequest(
        plan_id="plan-a",
        event_id="event-a",
        symbol="MES1!",
        quantity=1,
    )

    mutation = reserve_exposure(state, request, max_contracts=1)

    assert mutation.approved is True
    assert mutation.expected_revision == 7
    assert mutation.next_state.revision == 8
    assert mutation.next_state.reserved_contracts_total == 1
    assert mutation.reservation.plan_id == "plan-a"
    assert mutation.reservation.event_id == "event-a"
    assert mutation.reservation.symbol == "MES1!"
    assert mutation.reservation.quantity == 1


def test_distinct_plan_is_rejected_after_first_reservation_is_committed():
    initial = ExposureAdmissionState(
        account_id="paper-primary",
        session_id="CME-2026-09-03",
        revision=11,
        reserved_contracts_total=0,
    )
    first = reserve_exposure(
        initial,
        ExposureAdmissionRequest("plan-a", "event-a", "MES1!", 1),
        max_contracts=1,
    )
    second = reserve_exposure(
        first.next_state,
        ExposureAdmissionRequest("plan-b", "event-b", "MNQ1!", 1),
        max_contracts=1,
    )

    assert first.approved is True
    assert second.approved is False
    assert second.reason_code == "OPEN_ORDER_CONFLICT"
    assert second.expected_revision == first.next_state.revision
    assert second.next_state == first.next_state


def test_release_mutation_decrements_only_the_owned_quantity_and_advances_revision():
    state = ExposureAdmissionState(
        account_id="paper-primary",
        session_id="CME-2026-09-03",
        revision=21,
        reserved_contracts_total=3,
    )
    reservation = ExposureReservation(
        account_id="paper-primary",
        session_id="CME-2026-09-03",
        plan_id="plan-a",
        event_id="event-a",
        symbol="MES1!",
        quantity=1,
        admitted_revision=20,
    )

    mutation = release_exposure(state, reservation)

    assert mutation.approved is True
    assert mutation.reason_code == "RELEASED"
    assert mutation.expected_revision == 21
    assert mutation.next_state.revision == 22
    assert mutation.next_state.reserved_contracts_total == 2
    assert mutation.reservation == reservation


def test_release_mutation_fails_closed_on_wrong_account_or_session():
    state = ExposureAdmissionState("paper-primary", "CME-2026-09-03", 4, 1)
    wrong_account = ExposureReservation(
        "paper-other", "CME-2026-09-03", "plan-a", "event-a", "MES1!", 1, 4
    )
    wrong_session = ExposureReservation(
        "paper-primary", "CME-2026-09-04", "plan-a", "event-a", "MES1!", 1, 4
    )

    rejected_account = release_exposure(state, wrong_account)
    rejected_session = release_exposure(state, wrong_session)

    for rejected in (rejected_account, rejected_session):
        assert rejected.approved is False
        assert rejected.reason_code == "RESERVATION_SCOPE_MISMATCH"
        assert rejected.expected_revision == state.revision
        assert rejected.next_state == state


def test_release_mutation_fails_closed_instead_of_underflowing_reserved_exposure():
    state = ExposureAdmissionState("paper-primary", "CME-2026-09-03", 4, 0)
    reservation = ExposureReservation(
        "paper-primary", "CME-2026-09-03", "plan-a", "event-a", "MES1!", 1, 4
    )

    rejected = release_exposure(state, reservation)

    assert rejected.approved is False
    assert rejected.reason_code == "RESERVATION_NOT_AVAILABLE"
    assert rejected.expected_revision == state.revision
    assert rejected.next_state == state
