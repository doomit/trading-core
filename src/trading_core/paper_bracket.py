from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


@dataclass(frozen=True)
class PaperBracketRelationship:
    """Durable broker-neutral identity/state for one entry's protective OCO bracket."""

    parent_order_id: str
    bracket_id: str
    oco_group_id: str
    stop_order_id: str
    target_order_id: str
    original_quantity: int
    remaining_quantity: int
    cancelled_order_ids: tuple[str, ...] = ()
    status: str = "ACTIVE"

    @property
    def active_stop_quantity(self) -> int:
        if self.status != "ACTIVE" or self.stop_order_id in self.cancelled_order_ids:
            return 0
        return self.remaining_quantity

    @property
    def active_target_quantity(self) -> int:
        if self.status != "ACTIVE" or self.target_order_id in self.cancelled_order_ids:
            return 0
        return self.remaining_quantity


def build_paper_bracket(*, parent_order_id: str, quantity: int) -> PaperBracketRelationship:
    """Create deterministic child/OCO identities from an immutable entry order identity."""

    parent = _nonempty(parent_order_id, "parent_order_id")
    if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
        raise ValueError("quantity must be a positive integer")

    identity = hashlib.sha256(f"{parent}|PAPER_BRACKET_V1".encode()).hexdigest()
    suffix = identity[:32]
    return PaperBracketRelationship(
        parent_order_id=parent,
        bracket_id=f"paper-bracket:{suffix}",
        oco_group_id=f"paper-oco:{suffix}",
        stop_order_id=f"paper-order:stop:{suffix}",
        target_order_id=f"paper-order:target:{suffix}",
        original_quantity=quantity,
        remaining_quantity=quantity,
    )


def apply_oco_fill(
    relationship: PaperBracketRelationship,
    *,
    filled_order_id: str,
    filled_quantity: int,
) -> PaperBracketRelationship:
    """Apply a child fill while preserving OCO and remaining-stop continuity."""

    if not isinstance(relationship, PaperBracketRelationship):
        raise TypeError("relationship must be PaperBracketRelationship")
    order_id = _nonempty(filled_order_id, "filled_order_id")
    if order_id not in {relationship.stop_order_id, relationship.target_order_id}:
        raise ValueError("filled_order_id is not a child of this bracket")
    if relationship.status != "ACTIVE" or order_id in relationship.cancelled_order_ids:
        raise ValueError("bracket child is not active")
    if isinstance(filled_quantity, bool) or not isinstance(filled_quantity, int) or filled_quantity < 1:
        raise ValueError("filled_quantity must be a positive integer")
    if filled_quantity > relationship.remaining_quantity:
        raise ValueError("filled_quantity exceeds remaining bracket quantity")

    remaining = relationship.remaining_quantity - filled_quantity
    if remaining > 0:
        return replace(relationship, remaining_quantity=remaining)

    sibling = (
        relationship.target_order_id
        if order_id == relationship.stop_order_id
        else relationship.stop_order_id
    )
    cancelled = relationship.cancelled_order_ids
    if sibling not in cancelled:
        cancelled = (*cancelled, sibling)
    return replace(
        relationship,
        remaining_quantity=0,
        cancelled_order_ids=cancelled,
        status="CLOSED",
    )


__all__ = ["PaperBracketRelationship", "apply_oco_fill", "build_paper_bracket"]
