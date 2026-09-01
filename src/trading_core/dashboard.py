from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from importlib.resources import files
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

CANONICAL_STAGES = ["EVENT_CREATED", "PR_COMMENT_CREATED", "BRAIN_TRIGGERED", "PLAN_WRITTEN", "PLAN_VALIDATED", "EXECUTOR_RECEIVED", "RISK_DECIDED", "PAPER_ORDERED", "PAPER_FILLED_OR_REJECTED", "COMPLETED"]
SOURCE_TO_SUBSYSTEM = {"github_feed": "market_feed", "azure_event_producer": "azure_event_producer", "chatgpt_event_task": "chatgpt_brain", "azure_executor": "azure_executor", "risk_gateway": "paper_account", "paper_broker": "paper_account"}
DEFAULT_SUMMARIES = {"market_feed": "No correlated market-feed health receipt yet", "azure_event_producer": "No Azure event-producer receipt yet", "chatgpt_brain": "No ChatGPT event-task receipt yet", "azure_executor": "No Azure executor receipt yet", "paper_account": "No paper execution receipt yet"}
FRESH_SECONDS = {"market_feed": 90, "azure_event_producer": 300, "chatgpt_brain": 300, "azure_executor": 300, "paper_account": 300}
EXPECTED_FEED_SYMBOLS = ("MES", "MNQ")
STUCK_SECONDS = 300


def load_dashboard_schema() -> dict[str, Any]:
    return json.loads(files("trading_core.schemas").joinpath("trading_dashboard_v1.schema.json").read_text(encoding="utf-8"))


def dashboard_validator() -> Draft202012Validator:
    schema = load_dashboard_schema(); Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_dashboard(document: dict[str, Any]) -> None:
    dashboard_validator().validate(document)


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _try_parse_time(value):
    if not isinstance(value, str):
        return None
    try:
        return _parse_time(value)
    except (TypeError, ValueError):
        return None


def _last_non_null(receipts: list[dict], key: str):
    for receipt in reversed(receipts):
        value = receipt.get(key)
        if value is not None: return value
    return None


def _decision(receipts: list[dict]):
    for receipt in reversed(receipts):
        value = (receipt.get("details") or {}).get("decision")
        if value in {"LONG", "SHORT", "NO_TRADE", "HOLD", "EXIT", "UPDATE"}: return value
    return None


def _terminal_required_stages(receipts: list[dict], terminal_receipt: dict) -> list[str]:
    terminal_stage = terminal_receipt["stage"]
    if terminal_stage == "COMPLETED" and _decision(receipts) in {"NO_TRADE", "HOLD"}:
        i = CANONICAL_STAGES.index("EXECUTOR_RECEIVED"); return CANONICAL_STAGES[:i + 1] + ["COMPLETED"]
    return CANONICAL_STAGES[:CANONICAL_STAGES.index(terminal_stage) + 1]


def _event_summary(event_id: str, receipts: list[dict]) -> dict:
    receipts = sorted(receipts, key=lambda x: _parse_time(x["occurred_at"])); current = max(receipts, key=lambda x: CANONICAL_STAGES.index(x["stage"])); current_index = CANONICAL_STAGES.index(current["stage"])
    completed = [x for x in receipts if x["stage"] == "COMPLETED"]
    rejects = [x for x in receipts if x["stage"] == "RISK_DECIDED" and x["status"] == "REJECTED"]
    terminal = completed[-1] if completed else (rejects[-1] if rejects else None); observed = {x["stage"] for x in receipts}; blocker = None
    required = _terminal_required_stages(receipts, terminal) if terminal else CANONICAL_STAGES[:current_index + 1]
    for stage in required:
        if stage not in observed: blocker = stage; break
    if terminal is None and blocker is None and current_index + 1 < len(CANONICAL_STAGES): blocker = CANONICAL_STAGES[current_index + 1]
    latency = None; reason = None
    if terminal:
        reason = terminal.get("reason_code") or terminal.get("details", {}).get("terminal_reason")
        latency = int((_parse_time(terminal["occurred_at"]) - _parse_time(receipts[0]["occurred_at"])).total_seconds() * 1000)
    return {"event_id": event_id, "plan_id": _last_non_null(receipts, "plan_id"), "symbol": _last_non_null(receipts, "symbol"), "decision": _decision(receipts), "current_stage": current["stage"], "first_blocker_stage": blocker, "terminal_reason": reason, "end_to_end_latency_ms": latency}


def _subsystem_state(activities: list[dict], paper: dict, generated_at: str) -> dict:
    now = _parse_time(generated_at)
    subsystems = {key: {"status": "WAITING", "updated_at": None, "summary": summary, "age_seconds": None, "freshness": "UNSEEN"} for key, summary in DEFAULT_SUMMARIES.items()}
    latest = {}
    for activity in sorted(activities, key=lambda x: _parse_time(x["occurred_at"])):
        subsystem = SOURCE_TO_SUBSYSTEM.get(activity["source"])
        if subsystem: latest[subsystem] = activity
    for subsystem, activity in latest.items():
        age = max(0, int((now - _parse_time(activity["occurred_at"])).total_seconds())); stale = age > FRESH_SECONDS[subsystem]
        status = "FAILING" if activity["status"] == "FAIL" else ("WAITING" if activity["status"] == "WAITING" else "HEALTHY")
        if stale and status == "HEALTHY": status = "DEGRADED"
        reason = activity.get("reason_code"); summary = f"latest {activity['stage']} = {activity['status']}"
        if reason: summary += f" ({reason})"
        if stale: summary += f"; stale {age}s"
        subsystems[subsystem] = {"status": status, "updated_at": activity["occurred_at"], "summary": summary, "age_seconds": age, "freshness": "STALE" if stale else "FRESH"}
    if paper.get("paused") or paper.get("kill_switch"):
        base = subsystems["paper_account"]; base.update(status="FAILING" if base["status"] == "FAILING" else "DEGRADED", summary="paper execution is paused or kill switch is active")
    return subsystems


def _feed_freshness(activities: list[dict], generated_at: str) -> dict[str, dict]:
    now = _parse_time(generated_at)
    latest_by_symbol: dict[str, dict] = {}
    for activity in sorted(activities, key=lambda x: _parse_time(x["occurred_at"])):
        symbol = activity.get("symbol")
        if activity.get("source") == "github_feed" and symbol in EXPECTED_FEED_SYMBOLS:
            latest_by_symbol[symbol] = activity
    result = {}
    for symbol in EXPECTED_FEED_SYMBOLS:
        activity = latest_by_symbol.get(symbol)
        if activity is None:
            result[symbol] = {"updated_at": None, "age_seconds": None, "freshness": "UNSEEN"}
            continue
        age = max(0, int((now - _parse_time(activity["occurred_at"])).total_seconds()))
        result[symbol] = {"updated_at": activity["occurred_at"], "age_seconds": age, "freshness": "STALE" if age > FRESH_SECONDS["market_feed"] else "FRESH"}
    return result


def _scheduled_deep_brain_state(heartbeat: dict, generated_at: str) -> tuple[dict, bool]:
    now = _parse_time(generated_at)
    state = heartbeat.get("state")
    updated_at = heartbeat.get("completed_at") if state in {"COMPLETE", "FAILED"} else heartbeat.get("started_at")
    updated_dt = _try_parse_time(updated_at)
    age = max(0, int((now - updated_dt).total_seconds())) if updated_dt else None
    trusted = heartbeat.get("schema") == "deep_brain_status_v1" and heartbeat.get("paper_only") is True and state in {"RUNNING", "COMPLETE", "FAILED"}
    fresh = False
    if trusted and state == "COMPLETE":
        next_expected = _try_parse_time(heartbeat.get("next_expected_at"))
        fresh = updated_dt is not None and next_expected is not None and now <= next_expected
    elif trusted and state == "RUNNING":
        lease_expires = _try_parse_time(heartbeat.get("lease_expires_at"))
        fresh = updated_dt is not None and lease_expires is not None and now <= lease_expires
    projection = {
        "state": state if state in {"RUNNING", "COMPLETE", "FAILED"} else "FAILED",
        "freshness": "FRESH" if fresh else "STALE",
        "context_version": heartbeat.get("context_version"),
        "last_completed_context_version": heartbeat.get("last_completed_context_version"),
        "updated_at": updated_at if updated_dt else None,
        "next_expected_at": heartbeat.get("next_expected_at") if _try_parse_time(heartbeat.get("next_expected_at")) else None,
        "age_seconds": age,
        "run_id": heartbeat.get("run_id"),
        "worker_id": heartbeat.get("worker_id"),
        "outputs_count": len(heartbeat.get("outputs") or []),
        "skipped_symbols": [x.get("symbol") for x in (heartbeat.get("skipped_symbols") or []) if isinstance(x, dict) and x.get("symbol")],
    }
    return projection, fresh


def _overall_status(subsystems: dict) -> str:
    statuses = {x["status"] for x in subsystems.values()}
    for value in ("FAILING", "DEGRADED", "WAITING"):
        if value in statuses: return value
    return "HEALTHY"


def build_dashboard_state(activities: list[dict], paper: dict, generated_at: str, scheduled_deep_brain: dict | None = None) -> dict:
    ordered = sorted(activities, key=lambda x: _parse_time(x["occurred_at"])); now = _parse_time(generated_at); by_event = defaultdict(list)
    for activity in ordered: by_event[activity["event_id"]].append(activity)
    summaries = {eid: _event_summary(eid, receipts) for eid, receipts in by_event.items()}
    current_event = None
    if by_event:
        newest = max(by_event, key=lambda eid: max(_parse_time(x["occurred_at"]) for x in by_event[eid])); current_event = summaries[newest]
    subsystems = _subsystem_state(ordered, paper, generated_at)
    feed_freshness = _feed_freshness(ordered, generated_at)
    stuck = []
    for eid, receipts in by_event.items():
        summary = summaries[eid]
        if summary["first_blocker_stage"] is not None:
            age = max(0, int((now - max(_parse_time(x["occurred_at"]) for x in receipts)).total_seconds()))
            if age > STUCK_SECONDS: stuck.append({"event_id": eid, "age_seconds": age, "first_blocker_stage": summary["first_blocker_stage"]})
    risk_rejects = [{"event_id": x["event_id"], "occurred_at": x["occurred_at"], "reason_code": x.get("reason_code")} for x in ordered if x["stage"] == "RISK_DECIDED" and x["status"] == "REJECTED"][-10:]
    blockers = [name for name, state in subsystems.items() if state["status"] != "HEALTHY"]
    blockers.extend(f"market_feed:{symbol}" for symbol, state in feed_freshness.items() if state["freshness"] != "FRESH")
    deep_projection = None
    if scheduled_deep_brain is not None:
        deep_projection, deep_fresh = _scheduled_deep_brain_state(scheduled_deep_brain, generated_at)
        if not deep_fresh: blockers.append("scheduled_deep_brain")
    if paper.get("paused"): blockers.append("paper_paused")
    if paper.get("kill_switch"): blockers.append("kill_switch")
    if stuck: blockers.append("stuck_work")
    blockers = list(dict.fromkeys(blockers))
    return {"schema": "trading_dashboard_v1", "generated_at": generated_at, "overall_status": _overall_status(subsystems), "paper_ready": not blockers, "readiness_blockers": blockers, "subsystems": subsystems, "feed_freshness": feed_freshness, "scheduled_deep_brain": deep_projection, "current_event": current_event, "paper": dict(paper), "stuck_work": stuck, "risk_rejects": risk_rejects, "recent_activity": ordered[-20:]}


__all__ = ["CANONICAL_STAGES", "build_dashboard_state", "dashboard_validator", "load_dashboard_schema", "validate_dashboard"]
