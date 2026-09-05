from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from typing import Any


def _nonempty(value: str, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty string")
    return value


def _quantity(value: Any, field: str, *, allow_zero: bool = False) -> int:
    minimum = 0 if allow_zero else 1
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        qualifier = "nonnegative" if allow_zero else "positive"
        raise ValueError(f"{field} must be a {qualifier} integer")
    return value


def _relationship_ids(parent_order_id: str) -> tuple[str, str, str, str]:
    identity = hashlib.sha256(f"{parent_order_id}|PAPER_BRACKET_V1".encode()).hexdigest()
    suffix = identity[:32]
    return (
        f"paper-bracket:{suffix}",
        f"paper-oco:{suffix}",
        f"paper-order:stop:{suffix}",
        f"paper-order:target:{suffix}",
    )


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

    def to_record(self) -> dict[str, Any]:
        return {
            "schema": "paper_bracket_relationship_v1",
            "parent_order_id": self.parent_order_id,
            "bracket_id": self.bracket_id,
            "oco_group_id": self.oco_group_id,
            "stop_order_id": self.stop_order_id,
            "target_order_id": self.target_order_id,
            "original_quantity": self.original_quantity,
            "remaining_quantity": self.remaining_quantity,
            "cancelled_order_ids": list(self.cancelled_order_ids),
            "status": self.status,
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> PaperBracketRelationship:
        if not isinstance(record, dict) or record.get("schema") != "paper_bracket_relationship_v1":
            raise ValueError("record must be paper_bracket_relationship_v1")
        cancelled = record.get("cancelled_order_ids", [])
        if not isinstance(cancelled, list) or any(not isinstance(value, str) or not value for value in cancelled):
            raise ValueError("cancelled_order_ids must be a list of non-empty strings")
        relationship = cls(
            parent_order_id=_nonempty(record.get("parent_order_id"), "parent_order_id"),
            bracket_id=_nonempty(record.get("bracket_id"), "bracket_id"),
            oco_group_id=_nonempty(record.get("oco_group_id"), "oco_group_id"),
            stop_order_id=_nonempty(record.get("stop_order_id"), "stop_order_id"),
            target_order_id=_nonempty(record.get("target_order_id"), "target_order_id"),
            original_quantity=_quantity(record.get("original_quantity"), "original_quantity"),
            remaining_quantity=_quantity(record.get("remaining_quantity"), "remaining_quantity", allow_zero=True),
            cancelled_order_ids=tuple(cancelled),
            status=_nonempty(record.get("status"), "status"),
        )
        if relationship.status not in {"ACTIVE", "CLOSED"}:
            raise ValueError("status must be ACTIVE or CLOSED")
        if relationship.remaining_quantity > relationship.original_quantity:
            raise ValueError("remaining_quantity cannot exceed original_quantity")
        expected_ids = _relationship_ids(relationship.parent_order_id)
        actual_ids = (
            relationship.bracket_id,
            relationship.oco_group_id,
            relationship.stop_order_id,
            relationship.target_order_id,
        )
        if actual_ids != expected_ids:
            raise ValueError("durable bracket identity does not match parent_order_id")
        return relationship


def build_paper_bracket(*, parent_order_id: str, quantity: int) -> PaperBracketRelationship:
    """Create deterministic child/OCO identities from an immutable entry order identity."""

    parent = _nonempty(parent_order_id, "parent_order_id")
    quantity = _quantity(quantity, "quantity")
    bracket_id, oco_group_id, stop_order_id, target_order_id = _relationship_ids(parent)
    return PaperBracketRelationship(
        parent_order_id=parent,
        bracket_id=bracket_id,
        oco_group_id=oco_group_id,
        stop_order_id=stop_order_id,
        target_order_id=target_order_id,
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
    filled_quantity = _quantity(filled_quantity, "filled_quantity")
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
