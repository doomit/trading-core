from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import re
from typing import Iterable, Mapping


ACTIVITY_SCHEMA = "trading_activity_v1"
STATUS_SCHEMA = "trading_system_status_v1"

PIPELINE_STAGES = (
    "MARKET_INGEST",
    "MARKET_BUILD",
    "EVENT_DETECT",
    "BRAIN_REQUEST",
    "BRAIN_DECIDE",
    "PLAN_VALIDATE",
    "RISK_GATE",
    "ORDER_EXECUTE",
    "POSITION_MANAGE",
    "TRADE_COMPLETE",
)
STAGE_STATES = frozenset({"HEALTHY", "ACTIVE", "IDLE", "WAITING", "BLOCKED", "ERROR"})
ACTIVITY_STATUSES = frozenset({"SUCCESS", "ACTIVE", "WAITING", "ERROR", "REJECTED", "INFO"})
ACTOR_TYPES = frozenset({"AZURE", "CHATGPT", "GITHUB_ACTION", "GITHUB_VALIDATION", "SCHEDULER"})
ERROR_SEVERITIES = frozenset({"WARNING", "ERROR", "CRITICAL"})
LIFECYCLE_STAGES = PIPELINE_STAGES[2:]

_SECRET_KEY = re.compile(
    r"(?:secret|password|token|authorization|function[_-]?key|broker[_-]?key|api[_-]?key|credential)",
    re.IGNORECASE,
)


def _iso_from_ms(epoch_ms: int) -> str:
    return datetime.fromtimestamp(int(epoch_ms) / 1000.0, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_time(value: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    parsed = datetime.fromisoformat(text)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must be timezone-aware")
    return parsed


def _secret_path(value, prefix=""):
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            path = f"{prefix}.{key_text}" if prefix else key_text
            if _SECRET_KEY.search(key_text):
                return path
            found = _secret_path(child, path)
            if found:
                return found
    elif isinstance(value, (list, tuple)):
        for index, child in enumerate(value):
            path = f"{prefix}[{index}]" if prefix else f"[{index}]"
            found = _secret_path(child, path)
            if found:
                return found
    return None


def validate_activity(activity: dict):
    if not isinstance(activity, dict):
        return "activity_not_object", {}
    if activity.get("schema") != ACTIVITY_SCHEMA:
        return "invalid_schema", {}
    for field in (
        "activity_id", "occurred_at", "actor_name", "event_type", "event_id",
        "correlation_id", "producer_version",
    ):
        value = activity.get(field)
        if not isinstance(value, str) or not value:
            return "missing_or_invalid_field", {"field": field}
    if activity.get("stage") not in PIPELINE_STAGES:
        return "invalid_stage", {"stage": activity.get("stage")}
    if activity.get("status") not in ACTIVITY_STATUSES:
        return "invalid_status", {"status": activity.get("status")}
    if activity.get("actor_type") not in ACTOR_TYPES:
        return "invalid_actor_type", {"actor_type": activity.get("actor_type")}
    try:
        _parse_time(activity["occurred_at"])
    except (TypeError, ValueError):
        return "invalid_occurred_at", {}
    if "latency_ms" in activity:
        latency = activity["latency_ms"]
        if isinstance(latency, bool) or not isinstance(latency, int) or latency < 0:
            return "invalid_latency_ms", {}
    error = activity.get("error")
    if error is not None:
        if not isinstance(error, dict):
            return "invalid_error", {}
        for field in ("code", "severity", "first_seen_at", "last_seen_at", "recoverable", "summary"):
            if field not in error:
                return "invalid_error", {"field": field}
        if not isinstance(error["code"], str) or not error["code"]:
            return "invalid_error", {"field": "code"}
        if error["severity"] not in ERROR_SEVERITIES:
            return "invalid_error", {"field": "severity"}
        if not isinstance(error["recoverable"], bool):
            return "invalid_error", {"field": "recoverable"}
        if not isinstance(error["summary"], str) or not error["summary"]:
            return "invalid_error", {"field": "summary"}
        try:
            _parse_time(error["first_seen_at"])
            _parse_time(error["last_seen_at"])
        except (TypeError, ValueError):
            return "invalid_error", {"field": "timestamp"}
    if activity.get("status") == "ERROR" and error is None:
        return "error_details_required", {}
    forbidden = _secret_path(activity)
    if forbidden:
        return "secret_field_forbidden", {"path": forbidden}
    return None, {}


def make_activity(
    *,
    activity_id: str,
    occurred_at: str,
    actor_type: str,
    actor_name: str,
    stage: str,
    event_type: str,
    status: str,
    event_id: str,
    correlation_id: str,
    producer_version: str,
    plan_id: str | None = None,
    trade_id: str | None = None,
    symbol: str | None = None,
    latency_ms: int | None = None,
    reason_code: str | None = None,
    context: dict | None = None,
    error: dict | None = None,
) -> dict:
    doc = {
        "schema": ACTIVITY_SCHEMA,
        "activity_id": str(activity_id),
        "occurred_at": str(occurred_at),
        "actor_type": str(actor_type),
        "actor_name": str(actor_name),
        "stage": str(stage),
        "event_type": str(event_type),
        "status": str(status),
        "event_id": str(event_id),
        "correlation_id": str(correlation_id),
        "producer_version": str(producer_version),
    }
    optional = {
        "plan_id": plan_id,
        "trade_id": trade_id,
        "symbol": symbol,
        "latency_ms": latency_ms,
        "reason_code": reason_code,
        "context": dict(context) if context is not None else None,
        "error": dict(error) if error is not None else None,
    }
    doc.update({key: value for key, value in optional.items() if value is not None})
    validation_error, details = validate_activity(doc)
    if validation_error:
        raise ValueError(f"invalid activity: {validation_error} {details}")
    return doc


def _activity_sort_key(activity: dict):
    try:
        return (_parse_time(activity["occurred_at"]), str(activity.get("activity_id", "")))
    except (KeyError, TypeError, ValueError):
        return (datetime.min.replace(tzinfo=timezone.utc), str(activity.get("activity_id", "")))


def _stage_state_from_activity(activity: dict) -> str:
    status = activity.get("status")
    if status == "ACTIVE":
        return "ACTIVE"
    if status == "WAITING":
        return "WAITING"
    if status == "ERROR":
        return "ERROR"
    if status in ("SUCCESS", "REJECTED", "INFO"):
        return "HEALTHY"
    return "WAITING"


def _market_stage(stage: str, market: dict, now_iso: str) -> dict:
    field = "ingest_healthy" if stage == "MARKET_INGEST" else "build_healthy"
    healthy = market.get(field)
    if healthy is True:
        return {"stage": stage, "status": "HEALTHY"}
    if healthy is None:
        return {"stage": stage, "status": "WAITING"}
    code = "MARKET_INGEST_STALE" if stage == "MARKET_INGEST" else "MARKET_BUILD_STALE"
    return {
        "stage": stage,
        "status": "ERROR",
        "error": {
            "stage": stage,
            "code": code,
            "severity": "ERROR",
            "first_seen_at": now_iso,
            "last_seen_at": now_iso,
            "recoverable": True,
            "summary": f"{stage} is not fresh",
        },
    }


def _event_groups(activities: Iterable[dict]) -> dict[str, list[dict]]:
    groups: dict[str, list[dict]] = {}
    for item in activities:
        if item.get("stage") not in LIFECYCLE_STAGES:
            continue
        event_id = item.get("event_id")
        if not isinstance(event_id, str) or not event_id:
            continue
        groups.setdefault(event_id, []).append(item)
    for group in groups.values():
        group.sort(key=_activity_sort_key)
    return groups


def _current_event_group(activities: list[dict]):
    groups = _event_groups(activities)
    unfinished = []
    for event_id, group in groups.items():
        completed = any(
            item.get("stage") == "TRADE_COMPLETE" and item.get("status") in ("SUCCESS", "REJECTED", "INFO")
            for item in group
        )
        if not completed:
            unfinished.append((event_id, group))
    if not unfinished:
        return None, []
    unfinished.sort(key=lambda pair: _activity_sort_key(pair[1][-1]))
    return unfinished[-1]


def _event_stage_projection(group: list[dict]) -> list[dict]:
    if not group:
        return [{"stage": stage, "status": "IDLE"} for stage in LIFECYCLE_STAGES]
    latest_by_stage = {}
    for item in group:
        stage = item.get("stage")
        if stage in LIFECYCLE_STAGES:
            latest_by_stage[stage] = item
    projected = []
    blocking_stage = None
    for stage in LIFECYCLE_STAGES:
        item = latest_by_stage.get(stage)
        if blocking_stage is not None:
            projected.append({"stage": stage, "status": "BLOCKED", "blocked_by": blocking_stage})
            continue
        if item is not None:
            state = _stage_state_from_activity(item)
            entry = {"stage": stage, "status": state}
            if state == "ERROR":
                error = dict(item["error"])
                error.setdefault("stage", stage)
                entry["error"] = error
                blocking_stage = stage
            projected.append(entry)
            continue
        projected.append({"stage": stage, "status": "WAITING"})
        blocking_stage = stage
    return projected


def _latest_for_stage(group: list[dict], stage: str):
    candidates = [item for item in group if item.get("stage") == stage]
    return candidates[-1] if candidates else None


def _attention_from_pipeline(pipeline: list[dict], *, now_iso: str, event_id: str | None):
    errors = [entry for entry in pipeline if entry.get("status") == "ERROR"]
    if not errors:
        return None
    root = errors[0]
    source_error = dict(root.get("error", {}))
    attention = {
        "stage": root["stage"],
        "code": source_error.get("code", "UNKNOWN_ERROR"),
        "severity": source_error.get("severity", "ERROR"),
        "first_seen_at": source_error.get("first_seen_at", now_iso),
        "last_seen_at": source_error.get("last_seen_at", now_iso),
        "recoverable": bool(source_error.get("recoverable", True)),
        "summary": source_error.get("summary", f"{root['stage']} failed"),
    }
    if event_id is not None:
        attention["event_id"] = event_id
    return attention


def _overall_from_pipeline(pipeline: list[dict]) -> str:
    if any(entry.get("status") == "ERROR" for entry in pipeline):
        return "ERROR"
    if any(entry.get("status") in ("WAITING", "BLOCKED") for entry in pipeline[:2]):
        return "DEGRADED"
    return "HEALTHY"


def refresh_market_projection(
    current: dict | None,
    *,
    market: dict,
    now_ms: int,
    watermark: int,
) -> dict:
    now_iso = _iso_from_ms(now_ms)
    if current is None:
        return project_current_status(
            activities=[],
            now_ms=now_ms,
            market=market,
            paper={},
            position={"side": "FLAT", "quantity": 0},
            projection={"watermark": int(watermark), "source": "azure"},
        )
    doc = deepcopy(current)
    doc["schema"] = STATUS_SCHEMA
    doc["as_of"] = now_iso
    projection = dict(doc.get("projection") or {})
    projection["watermark"] = max(int(projection.get("watermark", 0)), int(watermark))
    projection.setdefault("source", "azure")
    doc["projection"] = projection
    doc["market"] = deepcopy(market)
    pipeline = list(doc.get("pipeline") or [])
    if len(pipeline) != len(PIPELINE_STAGES):
        pipeline = project_current_status(
            activities=[], now_ms=now_ms, market=market,
            paper=doc.get("paper") or {}, position=doc.get("position") or {},
            projection=projection,
        )["pipeline"]
    else:
        pipeline[0] = _market_stage("MARKET_INGEST", market, now_iso)
        pipeline[1] = _market_stage("MARKET_BUILD", market, now_iso)
    doc["pipeline"] = pipeline
    event_id = (doc.get("current_event") or {}).get("event_id")
    doc["attention"] = _attention_from_pipeline(pipeline, now_iso=now_iso, event_id=event_id)
    doc["overall"] = _overall_from_pipeline(pipeline)
    doc.setdefault("current_event", None)
    doc.setdefault("brain", None)
    doc.setdefault("risk", None)
    doc.setdefault("paper", {})
    doc.setdefault("position", {})
    return doc


def project_current_status(
    *,
    activities: Iterable[dict],
    now_ms: int,
    market: dict,
    paper: dict | None = None,
    position: dict | None = None,
    projection: dict | None = None,
) -> dict:
    now_iso = _iso_from_ms(now_ms)
    valid = []
    for activity in activities:
        error, _ = validate_activity(activity)
        if error is None:
            valid.append(dict(activity))
    valid.sort(key=_activity_sort_key)
    event_id, current_group = _current_event_group(valid)
    pipeline = [
        _market_stage("MARKET_INGEST", market, now_iso),
        _market_stage("MARKET_BUILD", market, now_iso),
    ]
    if event_id is None:
        pipeline.extend({"stage": stage, "status": "IDLE"} for stage in LIFECYCLE_STAGES)
    else:
        pipeline.extend(_event_stage_projection(current_group))
    attention = _attention_from_pipeline(pipeline, now_iso=now_iso, event_id=event_id)
    current_event = None
    if event_id is not None:
        current_event = {
            "event_id": event_id,
            "correlation_id": current_group[-1].get("correlation_id", event_id),
            "started_at": current_group[0]["occurred_at"],
            "last_activity_at": current_group[-1]["occurred_at"],
            "last_stage": current_group[-1]["stage"],
        }
    brain_activity = _latest_for_stage(current_group, "BRAIN_DECIDE") if current_group else None
    risk_activity = _latest_for_stage(current_group, "RISK_GATE") if current_group else None
    brain = None if brain_activity is None else {
        "status": brain_activity["status"],
        "plan_id": brain_activity.get("plan_id"),
        "reason_code": brain_activity.get("reason_code"),
        "occurred_at": brain_activity["occurred_at"],
    }
    risk = None if risk_activity is None else {
        "status": risk_activity["status"],
        "plan_id": risk_activity.get("plan_id"),
        "reason_code": risk_activity.get("reason_code"),
        "occurred_at": risk_activity["occurred_at"],
    }
    return {
        "schema": STATUS_SCHEMA,
        "as_of": now_iso,
        "overall": _overall_from_pipeline(pipeline),
        "projection": dict(projection or {}),
        "market": deepcopy(market),
        "pipeline": pipeline,
        "current_event": current_event,
        "brain": brain,
        "risk": risk,
        "paper": dict(paper or {}),
        "position": dict(position or {}),
        "attention": attention,
    }


__all__ = [
    "ACTIVITY_SCHEMA",
    "STATUS_SCHEMA",
    "PIPELINE_STAGES",
    "STAGE_STATES",
    "ACTIVITY_STATUSES",
    "ACTOR_TYPES",
    "ERROR_SEVERITIES",
    "LIFECYCLE_STAGES",
    "make_activity",
    "project_current_status",
    "refresh_market_projection",
    "validate_activity",
]
