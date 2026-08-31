from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Protocol


_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,159}$")
_DECISIONS = {"LONG", "SHORT", "NO_TRADE", "HOLD", "EXIT", "UPDATE"}
_REQUIRED_FIELDS = {
    "schema",
    "plan_id",
    "trigger_event_id",
    "created_at",
    "valid_until",
    "symbol",
    "decision",
    "confidence",
    "analysis_summary",
}


class PickupStatus(str, Enum):
    WAITING = "WAITING"
    READY = "READY"
    REJECTED = "REJECTED"


class PlanReservationStatus(str, Enum):
    RESERVED = "RESERVED"
    DUPLICATE = "DUPLICATE"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True)
class PickupDecision:
    status: PickupStatus
    reason_code: str
    plan: dict[str, Any] | None = None


@dataclass(frozen=True)
class PlanReservationDecision:
    status: PlanReservationStatus
    plan_hash: str
    reason_code: str


@dataclass(frozen=True)
class EventRecord:
    event_id: str
    plan_id: str
    symbol: str
    created_at: datetime
    deadline: datetime

    def __post_init__(self) -> None:
        expected = expected_plan_id(self.event_id)
        if self.plan_id != expected:
            raise ValueError("plan_id must match deterministic event identity")
        if not isinstance(self.symbol, str) or not self.symbol:
            raise ValueError("symbol must be a non-empty string")
        if self.created_at.tzinfo is None or self.created_at.utcoffset() is None:
            raise ValueError("created_at must be timezone-aware")
        if self.deadline.tzinfo is None or self.deadline.utcoffset() is None:
            raise ValueError("deadline must be timezone-aware")
        if self.deadline <= self.created_at:
            raise ValueError("deadline must be after created_at")


class OrchestrationRepository(Protocol):
    def get_event(self, event_id: str) -> EventRecord | None: ...
    def create_event(self, record: EventRecord) -> bool: ...
    def get_plan_hash(self, plan_id: str) -> str | None: ...
    def create_plan_reservation(self, plan_id: str, plan_hash: str) -> bool: ...


class EventIdentityConflict(ValueError):
    pass


class PlanValidationError(ValueError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def expected_plan_id(event_id: str) -> str:
    if not isinstance(event_id, str) or not _EVENT_ID_RE.fullmatch(event_id):
        raise ValueError("event_id must be a safe 1-160 character runtime identity")
    return event_id


def canonical_plan_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def start_or_resume_event(
    repo: OrchestrationRepository,
    *,
    event_id: str,
    symbol: str,
    created_at: datetime,
    deadline: datetime,
) -> tuple[EventRecord, bool]:
    plan_id = expected_plan_id(event_id)
    candidate = EventRecord(
        event_id=event_id,
        plan_id=plan_id,
        symbol=symbol,
        created_at=created_at,
        deadline=deadline,
    )
    existing = repo.get_event(event_id)
    if existing is not None:
        if existing.plan_id != candidate.plan_id or existing.symbol != candidate.symbol:
            raise EventIdentityConflict("event_id is already bound to different immutable identity data")
        return existing, False

    if repo.create_event(candidate):
        return candidate, True

    existing = repo.get_event(event_id)
    if existing is None:
        raise RuntimeError("event create lost race without a readable durable record")
    if existing.plan_id != candidate.plan_id or existing.symbol != candidate.symbol:
        raise EventIdentityConflict("event_id race resolved to different immutable identity data")
    return existing, False


def reserve_plan_once(repo: OrchestrationRepository, plan: dict[str, Any]) -> PlanReservationDecision:
    plan_id = plan.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id must be a non-empty string before reservation")
    plan_hash = canonical_plan_hash(plan)
    existing_hash = repo.get_plan_hash(plan_id)
    if existing_hash is not None:
        if existing_hash == plan_hash:
            return PlanReservationDecision(PlanReservationStatus.DUPLICATE, plan_hash, "PLAN_ALREADY_RESERVED")
        return PlanReservationDecision(PlanReservationStatus.CONFLICT, plan_hash, "PLAN_ID_HASH_CONFLICT")

    if repo.create_plan_reservation(plan_id, plan_hash):
        return PlanReservationDecision(PlanReservationStatus.RESERVED, plan_hash, "PLAN_RESERVED")

    existing_hash = repo.get_plan_hash(plan_id)
    if existing_hash is None:
        raise RuntimeError("plan reservation lost race without a readable durable record")
    if existing_hash == plan_hash:
        return PlanReservationDecision(PlanReservationStatus.DUPLICATE, plan_hash, "PLAN_ALREADY_RESERVED")
    return PlanReservationDecision(PlanReservationStatus.CONFLICT, plan_hash, "PLAN_ID_HASH_CONFLICT")


def _schema_error(message: str) -> None:
    raise PlanValidationError("INVALID_PLAN_SCHEMA", message)


def _parse_aware_timestamp(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise PlanValidationError("INVALID_PLAN_TIME", f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise PlanValidationError("INVALID_PLAN_TIME", f"invalid {field}: {exc}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise PlanValidationError("INVALID_PLAN_TIME", f"{field} must be timezone-aware")
    return parsed


def _validate_current_schema(plan: dict[str, Any]) -> None:
    if not isinstance(plan, dict):
        _schema_error("plan must be an object")
    missing = sorted(_REQUIRED_FIELDS - set(plan))
    if missing:
        _schema_error(f"missing required fields: {', '.join(missing)}")
    if plan.get("schema") != "trading_plan_v1":
        _schema_error("schema must be trading_plan_v1")
    for field in ("plan_id", "trigger_event_id", "created_at", "valid_until", "symbol"):
        if not isinstance(plan.get(field), str) or not plan[field]:
            _schema_error(f"{field} must be a non-empty string")
    if plan.get("decision") not in _DECISIONS:
        _schema_error("decision is not supported")
    confidence = plan.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not 0 <= confidence <= 1:
        _schema_error("confidence must be a number from 0 to 1")
    analysis_summary = plan.get("analysis_summary")
    if not isinstance(analysis_summary, list) or not all(isinstance(item, str) for item in analysis_summary):
        _schema_error("analysis_summary must be an array of strings")
    if "regime" in plan and not isinstance(plan["regime"], dict):
        _schema_error("regime must be an object")
    if "scenarios" in plan and not isinstance(plan["scenarios"], list):
        _schema_error("scenarios must be an array")
    if "cancel_if" in plan:
        cancel_if = plan["cancel_if"]
        if not isinstance(cancel_if, list) or not all(isinstance(item, str) for item in cancel_if):
            _schema_error("cancel_if must be an array of strings")
    if "position_action" in plan and plan["position_action"] is not None and not isinstance(plan["position_action"], dict):
        _schema_error("position_action must be an object or null")


def validate_trading_plan(
    plan: dict[str, Any],
    *,
    expected_event_id: str,
    now: datetime,
    expected_symbol: str | None = None,
) -> None:
    expected_id = expected_plan_id(expected_event_id)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if expected_symbol is not None and (not isinstance(expected_symbol, str) or not expected_symbol):
        raise ValueError("expected_symbol must be a non-empty string when provided")

    _validate_current_schema(plan)
    if plan["trigger_event_id"] != expected_event_id:
        raise PlanValidationError("EVENT_ID_MISMATCH", "trigger_event_id does not match the event")
    if plan["plan_id"] != expected_id:
        raise PlanValidationError("PLAN_ID_MISMATCH", "plan_id does not match the deterministic event plan identity")
    if expected_symbol is not None and plan["symbol"] != expected_symbol:
        raise PlanValidationError("SYMBOL_MISMATCH", "plan symbol does not match the durable event symbol")

    created_at = _parse_aware_timestamp(plan["created_at"], "created_at")
    valid_until = _parse_aware_timestamp(plan["valid_until"], "valid_until")
    if valid_until <= created_at:
        raise PlanValidationError("INVALID_PLAN_TIME_ORDER", "valid_until must be after created_at")
    if created_at > now:
        raise PlanValidationError("PLAN_CREATED_IN_FUTURE", "plan created_at is later than the reference time")
    if now >= valid_until:
        raise PlanValidationError("PLAN_EXPIRED", "plan is expired")


def classify_plan_pickup(
    plan: dict[str, Any] | None,
    *,
    expected_event_id: str,
    now: datetime,
    expected_symbol: str | None = None,
) -> PickupDecision:
    expected_plan_id(expected_event_id)
    if plan is None:
        return PickupDecision(PickupStatus.WAITING, "PLAN_NOT_AVAILABLE", None)
    try:
        validate_trading_plan(
            plan,
            expected_event_id=expected_event_id,
            now=now,
            expected_symbol=expected_symbol,
        )
    except PlanValidationError as exc:
        return PickupDecision(PickupStatus.REJECTED, exc.reason_code, None)
    return PickupDecision(PickupStatus.READY, "PLAN_READY", plan)


__all__ = [
    "EventIdentityConflict",
    "EventRecord",
    "OrchestrationRepository",
    "PickupDecision",
    "PickupStatus",
    "PlanReservationDecision",
    "PlanReservationStatus",
    "PlanValidationError",
    "canonical_plan_hash",
    "classify_plan_pickup",
    "expected_plan_id",
    "reserve_plan_once",
    "start_or_resume_event",
    "validate_trading_plan",
]
