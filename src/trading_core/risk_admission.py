from __future__ import annotations

from dataclasses import dataclass

_SUPPORTED_SYMBOLS = frozenset({"MES1!", "MNQ1!"})


@dataclass(frozen=True)
class ExposureAdmissionState:
    account_id: str
    session_id: str
    revision: int
    reserved_contracts_total: int

    def __post_init__(self) -> None:
        if not isinstance(self.account_id, str) or not self.account_id.strip():
            raise ValueError("account_id must be non-empty")
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("session_id must be non-empty")
        if not isinstance(self.revision, int) or self.revision < 0:
            raise ValueError("revision must be a non-negative integer")
        if not isinstance(self.reserved_contracts_total, int) or self.reserved_contracts_total < 0:
            raise ValueError("reserved_contracts_total must be a non-negative integer")


@dataclass(frozen=True)
class ExposureAdmissionRequest:
    plan_id: str
    event_id: str
    symbol: str
    quantity: int

    def __post_init__(self) -> None:
        if not isinstance(self.plan_id, str) or not self.plan_id.strip():
            raise ValueError("plan_id must be non-empty")
        if not isinstance(self.event_id, str) or not self.event_id.strip():
            raise ValueError("event_id must be non-empty")
        if self.symbol not in _SUPPORTED_SYMBOLS:
            raise ValueError("symbol must be MES1! or MNQ1!")
        if not isinstance(self.quantity, int) or self.quantity < 1:
            raise ValueError("quantity must be a positive integer")


@dataclass(frozen=True)
class ExposureReservation:
    account_id: str
    session_id: str
    plan_id: str
    event_id: str
    symbol: str
    quantity: int
    admitted_revision: int


@dataclass(frozen=True)
class ExposureAdmissionMutation:
    approved: bool
    reason_code: str
    expected_revision: int
    next_state: ExposureAdmissionState
    reservation: ExposureReservation | None = None


def reserve_exposure(
    state: ExposureAdmissionState,
    request: ExposureAdmissionRequest,
    *,
    max_contracts: int,
) -> ExposureAdmissionMutation:
    """Build a revision-guarded reservation mutation for a storage-level CAS.

    This function is intentionally storage-neutral. A caller must persist the returned
    mutation only when the durable state revision still equals ``expected_revision``.
    That compare-and-set is what makes distinct concurrent plans single-winner.
    """
    if not isinstance(state, ExposureAdmissionState):
        raise TypeError("state must be ExposureAdmissionState")
    if not isinstance(request, ExposureAdmissionRequest):
        raise TypeError("request must be ExposureAdmissionRequest")
    if not isinstance(max_contracts, int) or max_contracts < 1:
        raise ValueError("max_contracts must be a positive integer")

    expected_revision = state.revision
    if state.reserved_contracts_total + request.quantity > max_contracts:
        return ExposureAdmissionMutation(
            approved=False,
            reason_code="OPEN_ORDER_CONFLICT",
            expected_revision=expected_revision,
            next_state=state,
        )

    next_state = ExposureAdmissionState(
        account_id=state.account_id,
        session_id=state.session_id,
        revision=state.revision + 1,
        reserved_contracts_total=state.reserved_contracts_total + request.quantity,
    )
    reservation = ExposureReservation(
        account_id=state.account_id,
        session_id=state.session_id,
        plan_id=request.plan_id,
        event_id=request.event_id,
        symbol=request.symbol,
        quantity=request.quantity,
        admitted_revision=next_state.revision,
    )
    return ExposureAdmissionMutation(
        approved=True,
        reason_code="APPROVED",
        expected_revision=expected_revision,
        next_state=next_state,
        reservation=reservation,
    )


def effective_exposure_contracts(
    *,
    open_contracts_by_plan: dict[str, int],
    reserved_contracts_by_plan: dict[str, int],
) -> int:
    """Count effective exposure across the reservation-to-OPEN handoff.

    A plan reservation counts before its execution is OPEN. Once that same plan is
    durably OPEN, the OPEN quantity replaces (rather than adds to) its still-surviving
    reservation until idempotent reservation release completes.
    """
    if not isinstance(open_contracts_by_plan, dict) or not isinstance(reserved_contracts_by_plan, dict):
        raise TypeError("exposure inputs must be dictionaries keyed by plan_id")

    def validated(source: dict[str, int], name: str) -> dict[str, int]:
        values: dict[str, int] = {}
        for plan_id, quantity in source.items():
            if not isinstance(plan_id, str) or not plan_id.strip():
                raise ValueError(f"{name} plan_id must be non-empty")
            if not isinstance(quantity, int) or quantity < 1:
                raise ValueError(f"{name} quantity must be a positive integer")
            values[plan_id] = quantity
        return values

    opened = validated(open_contracts_by_plan, "open exposure")
    reserved = validated(reserved_contracts_by_plan, "reserved exposure")
    for plan_id in opened.keys() & reserved.keys():
        if opened[plan_id] != reserved[plan_id]:
            raise ValueError("OPEN and reserved quantities disagree for the same plan")
    return sum(opened.values()) + sum(
        quantity for plan_id, quantity in reserved.items() if plan_id not in opened
    )


__all__ = [
    "ExposureAdmissionMutation",
    "ExposureAdmissionRequest",
    "ExposureAdmissionState",
    "ExposureReservation",
    "effective_exposure_contracts",
    "reserve_exposure",
]
