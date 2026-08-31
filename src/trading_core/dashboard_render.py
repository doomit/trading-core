from __future__ import annotations


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

SUBSYSTEM_LABELS = {
    "market_feed": "Market Feed",
    "azure_event_producer": "Azure Event Producer",
    "chatgpt_brain": "ChatGPT Brain",
    "azure_executor": "Azure Executor",
    "paper_account": "Paper Account",
}

STATUS_GLYPHS = {
    "HEALTHY": "✅",
    "WAITING": "⏳",
    "DEGRADED": "⚠️",
    "FAILING": "🚨",
    "PASS": "✅",
    "INFO": "ℹ️",
    "REJECTED": "⛔",
    "FAIL": "🚨",
    "FRESH": "✅",
    "STALE": "⚠️",
    "UNSEEN": "⏳",
}


def _cell(value) -> str:
    if value is None:
        return "—"
    return str(value).replace("|", "\\|").replace("\n", " ")


def _money(value) -> str:
    return f"${float(value):,.2f}"


def _age(value) -> str:
    return "—" if value is None else f"{value}s"


def _stage_line(current_stage: str, first_blocker_stage: str | None) -> str:
    try:
        current_index = CANONICAL_STAGES.index(current_stage)
    except ValueError:
        current_index = -1
    parts = []
    for index, stage in enumerate(CANONICAL_STAGES):
        if stage == first_blocker_stage:
            marker = "❌"
        elif index <= current_index:
            marker = "✅"
        else:
            marker = "·"
        parts.append(f"{marker} `{stage}`")
    return " → ".join(parts)


def render_dashboard(state: dict) -> str:
    overall = state["overall_status"]
    paper_ready = state.get("paper_ready")
    blockers = state.get("readiness_blockers", [])
    lines = [
        "# Trading E2E Dashboard",
        "",
        f"**{STATUS_GLYPHS.get(overall, '•')} {overall}** · generated `{state['generated_at']}`",
        "",
        "## Paper readiness",
        "",
    ]

    if paper_ready is None:
        lines.append("Readiness projection unavailable in this dashboard state.")
    else:
        lines.append(f"**{'READY' if paper_ready else 'NOT READY'}**")
        if blockers:
            lines.append(f"**Blockers:** {', '.join(f'`{_cell(item)}`' for item in blockers)}")
        else:
            lines.append("**Blockers:** none")

    lines.extend(
        [
            "",
            "## Feed freshness",
            "",
            "| Symbol | Freshness | Age | Updated |",
            "| --- | --- | ---: | --- |",
        ]
    )
    feed_freshness = state.get("feed_freshness", {})
    if not feed_freshness:
        lines.append("| — | — | — | No symbol-level feed freshness available |")
    else:
        for symbol in ("MES", "MNQ"):
            feed = feed_freshness.get(symbol)
            if feed is None:
                continue
            freshness = feed.get("freshness", "UNSEEN")
            lines.append(
                f"| {symbol} | {STATUS_GLYPHS.get(freshness, '•')} {freshness} | "
                f"{_age(feed.get('age_seconds'))} | {_cell(feed.get('updated_at'))} |"
            )

    lines.extend(
        [
            "",
            "## System health",
            "",
            "| Subsystem | Status | Updated | Summary |",
            "| --- | --- | --- | --- |",
        ]
    )

    for key, label in SUBSYSTEM_LABELS.items():
        subsystem = state["subsystems"][key]
        status = subsystem["status"]
        lines.append(
            f"| {label} | {STATUS_GLYPHS.get(status, '•')} {status} | "
            f"{_cell(subsystem['updated_at'])} | {_cell(subsystem['summary'])} |"
        )

    lines.extend(["", "## Current E2E event", ""])
    event = state.get("current_event")
    if not event:
        lines.append("No active correlated event.")
    else:
        lines.extend(
            [
                f"**Event:** `{event['event_id']}` · **Plan:** `{_cell(event.get('plan_id'))}` · "
                f"**Symbol:** `{_cell(event.get('symbol'))}` · **Decision:** `{_cell(event.get('decision'))}`",
                "",
                _stage_line(event["current_stage"], event.get("first_blocker_stage")),
                "",
                f"**Current stage:** `{event['current_stage']}`  ",
                f"**First blocker:** `{_cell(event.get('first_blocker_stage'))}`  ",
                f"**Terminal reason:** {_cell(event.get('terminal_reason'))}  ",
                f"**E2E latency:** {_cell(event.get('end_to_end_latency_ms'))} ms",
            ]
        )

    lines.extend(
        [
            "",
            "## Stuck work",
            "",
            "| Event | Age | First blocker |",
            "| --- | ---: | --- |",
        ]
    )
    stuck_work = state.get("stuck_work", [])
    if not stuck_work:
        lines.append("| — | — | No stuck work |")
    else:
        for item in stuck_work:
            lines.append(
                f"| `{_cell(item.get('event_id'))}` | {_age(item.get('age_seconds'))} | "
                f"`{_cell(item.get('first_blocker_stage'))}` |"
            )

    lines.extend(
        [
            "",
            "## Recent risk rejects",
            "",
            "| Time | Event | Reason |",
            "| --- | --- | --- |",
        ]
    )
    risk_rejects = state.get("risk_rejects", [])
    if not risk_rejects:
        lines.append("| — | — | No recent risk rejects |")
    else:
        for item in reversed(risk_rejects[-10:]):
            lines.append(
                f"| {_cell(item.get('occurred_at'))} | `{_cell(item.get('event_id'))}` | "
                f"{_cell(item.get('reason_code'))} |"
            )

    paper = state["paper"]
    lines.extend(
        [
            "",
            "## Paper account",
            "",
            f"**Equity:** {_money(paper['equity_usd'])} · **Realized:** {_money(paper['realized_pnl_usd'])} · "
            f"**Unrealized:** {_money(paper['unrealized_pnl_usd'])}",
            "",
            f"**Position:** `{_cell(paper['position'])}` · **Trades:** {paper['trade_count']} · "
            f"**Consecutive losses:** {paper['consecutive_losses']} · **Paused:** {paper['paused']} · "
            f"**Kill switch:** {paper['kill_switch']}",
            "",
            "## Recent material activity",
            "",
            "| Time | Event | Stage | Status | Source | Reason |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )

    activities = state.get("recent_activity", [])[-20:]
    if not activities:
        lines.append("| — | — | — | — | — | No material activity yet |")
    else:
        for item in reversed(activities):
            status = item["status"]
            lines.append(
                f"| {_cell(item['occurred_at'])} | `{_cell(item['event_id'])}` | `{_cell(item['stage'])}` | "
                f"{STATUS_GLYPHS.get(status, '•')} {status} | {_cell(item['source'])} | "
                f"{_cell(item.get('reason_code'))} |"
            )

    lines.extend(
        [
            "",
            "> Dashboard/status output is non-triggering. Trading Feed PR comments are ingress-only and are not used for dashboard updates.",
            "",
        ]
    )
    return "\n".join(lines)


__all__ = ["CANONICAL_STAGES", "render_dashboard"]
