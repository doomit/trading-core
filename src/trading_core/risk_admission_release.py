from __future__ import annotations

from .risk_admission import ExposureAdmissionMutation, ExposureAdmissionState, ExposureReservation


def release_exposure(
    state: ExposureAdmissionState,
    reservation: ExposureReservation,
) -> ExposureAdmissionMutation:
    """Build a revision-guarded reservation release mutation for durable CAS."""
    if not isinstance(state, ExposureAdmissionState):
        raise TypeError("state must be ExposureAdmissionState")
    if not isinstance(reservation, ExposureReservation):
        raise TypeError("reservation must be ExposureReservation")
    expected_revision = state.revision
    if reservation.account_id != state.account_id or reservation.session_id != state.session_id:
        return ExposureAdmissionMutation(
            False,
            "RESERVATION_SCOPE_MISMATCH",
            expected_revision,
            state,
            reservation,
        )
    if reservation.quantity > state.reserved_contracts_total:
        return ExposureAdmissionMutation(
            False,
            "RESERVATION_NOT_AVAILABLE",
            expected_revision,
            state,
            reservation,
        )
    next_state = ExposureAdmissionState(
        account_id=state.account_id,
        session_id=state.session_id,
        revision=state.revision + 1,
        reserved_contracts_total=state.reserved_contracts_total - reservation.quantity,
    )
    return ExposureAdmissionMutation(
        True,
        "RELEASED",
        expected_revision,
        next_state,
        reservation,
    )


__all__ = ["release_exposure"]
