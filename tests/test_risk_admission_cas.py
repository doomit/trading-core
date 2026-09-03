from trading_core.risk_admission import (
    ExposureAdmissionRequest,
    ExposureAdmissionState,
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
