from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from html import escape
from typing import Any, Iterable


SYMBOLS = ("MES", "MNQ")


@dataclass(frozen=True)
class DashboardThresholds:
    feed_stale_ms: int = 90_000
    github_mirror_stale_ms: int = 90_000
    cycle_late_ms: int = 30_000
    brain_late_ms: int = 20 * 60_000


def build_simple_paper_dashboard(
    *,
    now: datetime,
    thresholds: DashboardThresholds,
    market_snapshots: dict[str, dict[str, Any] | None],
    positions: dict[str, dict[str, Any] | None],
    plans: dict[str, dict[str, Any] | None],
    ingest_logs: Iterable[dict[str, Any]],
    plan_observation_logs: Iterable[dict[str, Any]],
    execution_logs: Iterable[dict[str, Any]],
    cycle_logs: Iterable[dict[str, Any]],
    account: dict[str, Any] | None,
    brain_run: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a read-only Simple Paper health projection from explicit runtime facts.

    This function is intentionally side-effect free. It derives health from timestamps
    and log outcomes supplied by the caller and never reads or mutates trading state.
    """
    _require_aware(now)

    ingest_by_symbol = _latest_by_symbol(ingest_logs, "logged_at")
    obs_by_symbol = _latest_by_symbol(plan_observation_logs, "pulled_at")
    exec_by_symbol = _latest_by_symbol(execution_logs, "executed_at")
    cycle_by_symbol = _latest_by_symbol(cycle_logs, "tick_at")

    result: dict[str, Any] = {
        "schema": "simple_paper_dashboard_v1",
        "generated_at": _iso(now),
        "overall_health": "GREEN",
        "reasons": [],
        "symbols": {},
        "account": dict(account) if isinstance(account, dict) else None,
        "brain_run": _brain_summary(brain_run),
    }

    severity = 0
    reasons: list[str] = []

    if account is None:
        severity = max(severity, 2)
        reasons.append("ACCOUNT_MISSING")

    for symbol in SYMBOLS:
        market = market_snapshots.get(symbol)
        position = positions.get(symbol)
        plan = plans.get(symbol)
        ingest = ingest_by_symbol.get(symbol)
        observation = obs_by_symbol.get(symbol)
        execution = exec_by_symbol.get(symbol)
        cycle = cycle_by_symbol.get(symbol)
        brain_symbol = _brain_symbol(brain_run, symbol)

        feed_age = _age_ms(now, market.get("latest_bar_end")) if isinstance(market, dict) else None
        mirror_age = _age_ms(now, market.get("github_mirrored_at")) if isinstance(market, dict) else None
        cycle_age = _age_ms(now, cycle.get("tick_at")) if isinstance(cycle, dict) else None
        brain_age = _age_ms(now, brain_run.get("generated_at")) if isinstance(brain_run, dict) else None

        symbol_view = {
            "feed_age_ms": feed_age,
            "github_market_mirror_age_ms": mirror_age,
            "market_to_webhook_latency_ms": ingest.get("market_to_webhook_latency_ms") if ingest else None,
            "db_write_latency_ms": ingest.get("db_write_latency_ms") if ingest else None,
            "github_mirror_latency_ms": ingest.get("github_mirror_latency_ms") if ingest else None,
            "brain_generation_latency_ms": observation.get("brain_generation_latency_ms") if observation else None,
            "plan_pickup_latency_ms": observation.get("plan_pickup_latency_ms") if observation else None,
            "cycle_heartbeat_age_ms": cycle_age,
            "position": dict(position) if isinstance(position, dict) else None,
            "current_plan": dict(plan) if isinstance(plan, dict) else None,
            "latest_execution": dict(execution) if isinstance(execution, dict) else None,
            "latest_ingest": dict(ingest) if isinstance(ingest, dict) else None,
            "latest_plan_observation": dict(observation) if isinstance(observation, dict) else None,
            "latest_cycle": dict(cycle) if isinstance(cycle, dict) else None,
            "brain": dict(brain_symbol) if isinstance(brain_symbol, dict) else None,
            "health": "GREEN",
            "reasons": [],
        }

        symbol_severity = 0
        symbol_reasons: list[str] = []

        def add(reason: str, level: int) -> None:
            nonlocal symbol_severity, severity
            token = f"{symbol}:{reason}"
            if token not in reasons:
                reasons.append(token)
            if token not in symbol_reasons:
                symbol_reasons.append(token)
            symbol_severity = max(symbol_severity, level)
            severity = max(severity, level)

        if market is None:
            add("MARKET_MISSING", 2)
        elif feed_age is None or feed_age > thresholds.feed_stale_ms:
            add("FEED_STALE", 2)

        if position is None:
            add("POSITION_MISSING", 2)

        if cycle is None:
            add("CYCLE_MISSING", 2)
        else:
            if cycle_age is None or cycle_age > thresholds.cycle_late_ms:
                add("CYCLE_LATE", 2)
            if cycle.get("outcome") == "ERROR" or cycle.get("error"):
                add("CYCLE_ERROR", 2)

        if ingest is None:
            add("INGEST_LOG_MISSING", 1)
        else:
            github_status = ingest.get("github_status")
            if github_status == "FAILED":
                add("GITHUB_MIRROR_FAILED", 1)
            elif github_status == "SUCCESS" and (
                mirror_age is None or mirror_age > thresholds.github_mirror_stale_ms
            ):
                add("GITHUB_MIRROR_STALE", 1)
            if ingest.get("error") and github_status != "FAILED":
                add("INGEST_ERROR", 1)

        if observation is not None and observation.get("outcome") in {"READ_FAILED", "INVALID"}:
            add("PLAN_OBSERVATION_ERROR", 1)

        if brain_run is None:
            add("BRAIN_RUN_MISSING", 1)
        else:
            if brain_age is None or brain_age > thresholds.brain_late_ms:
                add("BRAIN_LATE", 1)
            if isinstance(brain_symbol, dict) and brain_symbol.get("blocker"):
                add("BRAIN_BLOCKED", 1)
            if brain_run.get("evidence_error"):
                add("BRAIN_EVIDENCE_ERROR", 1)

        symbol_view["health"] = _health(symbol_severity)
        symbol_view["reasons"] = symbol_reasons
        result["symbols"][symbol] = symbol_view

    result["overall_health"] = _health(severity)
    result["reasons"] = reasons
    return result


def render_simple_paper_dashboard_html(dashboard: dict[str, Any]) -> str:
    """Render a compact read-only HTML view of a dashboard projection."""
    health = escape(str(dashboard.get("overall_health", "UNKNOWN")))
    account = dashboard.get("account") or {}
    balance = account.get("balance_usd")
    balance_text = f"{float(balance):,.2f}" if isinstance(balance, (int, float)) else "n/a"
    reasons = dashboard.get("reasons") or []
    reason_html = "".join(f"<li>{escape(str(reason))}</li>" for reason in reasons) or "<li>None</li>"

    rows: list[str] = []
    for symbol in SYMBOLS:
        info = (dashboard.get("symbols") or {}).get(symbol) or {}
        feed = _seconds(info.get("feed_age_ms"))
        cycle = _seconds(info.get("cycle_heartbeat_age_ms"))
        position = info.get("position") or {}
        plan = info.get("current_plan") or {}
        rows.append(
            "<tr>"
            f"<td>{escape(symbol)}</td>"
            f"<td>{escape(str(info.get('health', 'UNKNOWN')))}</td>"
            f"<td>{escape(feed)}</td>"
            f"<td>{escape(cycle)}</td>"
            f"<td>{escape(str(position.get('status', 'MISSING')))}</td>"
            f"<td>{escape(str(plan.get('plan_id', 'none')))}</td>"
            "</tr>"
        )

    return (
        "<!doctype html><html><head><meta charset='utf-8'><title>Simple Paper Dashboard</title></head><body>"
        f"<h1>Simple Paper</h1><h2>{health}</h2>"
        f"<p>Paper balance: ${balance_text}</p>"
        "<table><thead><tr><th>Symbol</th><th>Health</th><th>Feed age</th><th>Cycle age</th>"
        "<th>Position</th><th>Plan</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table><h3>Reasons</h3><ul>"
        + reason_html
        + "</ul></body></html>"
    )


def _latest_by_symbol(records: Iterable[dict[str, Any]], timestamp_field: str) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for record in records:
        if not isinstance(record, dict):
            continue
        symbol = str(record.get("symbol", "")).upper()
        if symbol not in SYMBOLS:
            continue
        current = latest.get(symbol)
        if current is None or _timestamp_key(record.get(timestamp_field)) > _timestamp_key(current.get(timestamp_field)):
            latest[symbol] = record
    return latest


def _brain_symbol(brain_run: dict[str, Any] | None, symbol: str) -> dict[str, Any] | None:
    if not isinstance(brain_run, dict):
        return None
    symbols = brain_run.get("symbols")
    if not isinstance(symbols, dict):
        return None
    value = symbols.get(symbol)
    return value if isinstance(value, dict) else None


def _brain_summary(brain_run: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(brain_run, dict):
        return None
    return {
        "run_id": brain_run.get("run_id"),
        "scheduled_run_time": brain_run.get("scheduled_run_time"),
        "generated_at": brain_run.get("generated_at"),
        "result": brain_run.get("result"),
        "evidence_published": brain_run.get("evidence_published"),
        "evidence_error": brain_run.get("evidence_error"),
    }


def _age_ms(now: datetime, value: Any) -> int | None:
    if value is None:
        return None
    try:
        age = int((now - _parse_time(value)).total_seconds() * 1000)
    except (TypeError, ValueError):
        return None
    return max(0, age)


def _timestamp_key(value: Any) -> float:
    try:
        return _parse_time(value).timestamp()
    except (TypeError, ValueError):
        return float("-inf")


def _parse_time(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    _require_aware(parsed)
    return parsed


def _require_aware(value: datetime) -> None:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _health(level: int) -> str:
    return ("GREEN", "DEGRADED", "RED")[max(0, min(2, level))]


def _seconds(milliseconds: Any) -> str:
    if not isinstance(milliseconds, (int, float)):
        return "n/a"
    return f"{milliseconds / 1000.0:.1f}s"


__all__ = [
    "DashboardThresholds",
    "build_simple_paper_dashboard",
    "render_simple_paper_dashboard_html",
]
