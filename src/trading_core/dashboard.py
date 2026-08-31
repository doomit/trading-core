from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker


CANONICAL_STAGES = [
    "EVENT_CREATED",
    "PR_COMMENT_CREATED",
    "BRAIN_TRIGGERED",
    "PLAN_WRITTEN",
    "PLAN_VALIDATED",
    "EXECUTOR_RECEIVED",
    "RISK_DECIDED",
    "PAPER_ORDERED",
    "PAPER_FILLED_OR_REJECTED",
    "COMPLETED",
]

SOURCE_TO_SUBSYSTEM = {
    "github_feed": "market_feed",
    "azure_event_producer": "azure_event_producer",
    "chatgpt_event_task": "chatgpt_brain",
    "azure_executor": "azure_executor",
    "risk_gateway": "paper_account",
    "paper_broker": "paper_account",
}

DEFAULT_SUMMARIES = {
    "market_feed": "No correlated market-feed health receipt yet",
    "azure_event_producer": "No Azure event-producer receipt yet",
    "chatgpt_brain": "No ChatGPT event-task receipt yet",
    "azure_executor": "No Azure executor receipt yet",
    "paper_account": "No paper execution receipt yet",
}


def load_dashboard_schema() -> dict[str, Any]:
    return json.loads(
        files("trading_core.schemas").joinpath("trading_dashboard_v1.schema.json").read_text(encoding="utf-8")
    )


def dashboard_validator() -> Draft202012Validator:
    schema = load_dashboard_schema()
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_dashboard(document: dict[str, Any]) -> None:
    dashboard_validator().validate(document)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _last_non_null(receipts: list[dict], key: str):
    for receipt in reversed(receipts):
        value = receipt.get(key)
        if value is not None:
            return value
    return None


def _decision(receipts: list[dict]):
    for receipt in reversed(receipts):
        details = receipt.get("details") or {}
        value = details.get("decision")
        if value in {"LONG", "SHORT", "NO_TRADE", "HOLD", "EXIT", "UPDATE"}:
            return value
    return None


def _terminal_required_stages(receipts: list[dict], terminal_receipt: dict) -> list[str]:
    terminal_stage = terminal_receipt["stage"]
    if terminal_stage == "COMPLETED" and _decision(receipts) in {"NO_TRADE", "HOLD"}:
        executor_index = CANONICAL_STAGES.index("EXECUTOR_RECEIVED")
        return CANONICAL_STAGES[: executor_index + 1] + ["COMPLETED"]
    terminal_index = CANONICAL_STAGES.index(terminal_stage)
    return CANONICAL_STAGES[: terminal_index + 1]


def _event_summary(event_id: str, receipts: list[dict]) -> dict:
    receipts = sorted(receipts, key=lambda item: _parse_time(item["occurred_at"]))
    current = max(receipts, key=lambda item: CANONICAL_STAGES.index(item["stage"]))
    current_index = CANONICAL_STAGES.index(current["stage"])

    completed = [item for item in receipts if item["stage"] == "COMPLETED"]
    risk_rejections = [
        item
        for item in receipts
        if item["stage"] == "RISK_DECIDED" and item["status"] == "REJECTED"
    ]
    terminal_receipt = completed[-1] if completed else (risk_rejections[-1] if risk_rejections else None)

    observed_stages = {item["stage"] for item in receipts}
    first_blocker = None
    if terminal_receipt is not None:
        required_stages = _terminal_required_stages(receipts, terminal_receipt)
        for stage in required_stages:
            if stage not in observed_stages:
                first_blocker = stage
                break
    else:
        for stage in CANONICAL_STAGES[: current_index + 1]:
            if stage not in observed_stages:
                first_blocker = stage
                break
        if first_blocker is None and current_index + 1 < len(CANONICAL_STAGES):
            first_blocker = CANONICAL_STAGES[current_index + 1]

    terminal_reason = None
    end_to_end_latency_ms = None
    if terminal_receipt is not None:
        terminal_reason = terminal_receipt.get("reason_code") or terminal_receipt.get("details", {}).get("terminal_reason")
        start = _parse_time(receipts[0]["occurred_at"])
        end = _parse_time(terminal_receipt["occurred_at"])
        end_to_end_latency_ms = int((end - start).total_seconds() * 1000)

    return {
        "event_id": event_id,
        "plan_id": _last_non_null(receipts, "plan_id"),
        "symbol": _last_non_null(receipts, "symbol"),
        "decision": _decision(receipts),
        "current_stage": current["stage"],
        "first_blocker_stage": first_blocker,
        "terminal_reason": terminal_reason,
        "end_to_end_latency_ms": end_to_end_latency_ms,
    }


def _subsystem_state(activities: list[dict], paper: dict, generated_at: str) -> dict:
    subsystems = {
        key: {"status": "WAITING", "updated_at": generated_at, "summary": summary}
        for key, summary in DEFAULT_SUMMARIES.items()
    }

    latest_by_subsystem: dict[str, dict] = {}
    for activity in sorted(activities, key=lambda item: _parse_time(item["occurred_at"])):
        subsystem = SOURCE_TO_SUBSYSTEM.get(activity["source"])
        if subsystem:
            latest_by_subsystem[subsystem] = activity

    for subsystem, activity in latest_by_subsystem.items():
        if activity["status"] == "FAIL":
            status = "FAILING"
        elif activity["status"] == "WAITING":
            status = "WAITING"
        else:
            status = "HEALTHY"
        reason = activity.get("reason_code")
        summary = f"latest {activity['stage']} = {activity['status']}"
        if reason:
            summary += f" ({reason})"
        subsystems[subsystem] = {
            "status": status,
            "updated_at": activity["occurred_at"],
            "summary": summary,
        }

    if paper.get("paused") or paper.get("kill_switch"):
        base = subsystems["paper_account"]
        subsystems["paper_account"] = {
            "status": "DEGRADED" if base["status"] != "FAILING" else "FAILING",
            "updated_at": generated_at,
            "summary": "paper execution is paused or kill switch is active",
        }
    return subsystems


def _overall_status(subsystems: dict) -> str:
    statuses = {item["status"] for item in subsystems.values()}
    if "FAILING" in statuses:
        return "FAILING"
    if "DEGRADED" in statuses:
        return "DEGRADED"
    if "WAITING" in statuses:
        return "WAITING"
    return "HEALTHY"


def build_dashboard_state(activities: list[dict], paper: dict, generated_at: str) -> dict:
    ordered = sorted(activities, key=lambda item: _parse_time(item["occurred_at"]))
    by_event: dict[str, list[dict]] = defaultdict(list)
    for activity in ordered:
        by_event[activity["event_id"]].append(activity)

    current_event = None
    if by_event:
        newest_event_id = max(
            by_event,
            key=lambda event_id: max(_parse_time(item["occurred_at"]) for item in by_event[event_id]),
        )
        current_event = _event_summary(newest_event_id, by_event[newest_event_id])

    subsystems = _subsystem_state(ordered, paper, generated_at)
    return {
        "schema": "trading_dashboard_v1",
        "generated_at": generated_at,
        "overall_status": _overall_status(subsystems),
        "subsystems": subsystems,
        "current_event": current_event,
        "paper": dict(paper),
        "recent_activity": ordered[-20:],
    }


__all__ = [
    "CANONICAL_STAGES",
    "build_dashboard_state",
    "dashboard_validator",
    "load_dashboard_schema",
    "validate_dashboard",
]
