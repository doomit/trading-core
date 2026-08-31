from __future__ import annotations

import calendar
import json
import math
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable, Iterable, Mapping

COST_API_VERSION = "2023-11-01"
DEFAULT_MAX_CACHE_AGE_HOURS = 23.0
DEFAULT_MAX_ATTEMPTS = 6
DEFAULT_MAX_TOTAL_WAIT_SECONDS = 1200
FALLBACK_RETRY_DELAYS = (30, 60, 120, 240, 480)
TRANSIENT_HTTP_STATUSES = {429, 503, 504}

QPU_RETRY_HEADER = "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after"
CONSUMPTION_RETRY_HEADER = "x-ms-ratelimit-microsoft.consumption-retry-after"
STANDARD_RETRY_HEADER = "retry-after"


class CostReportError(RuntimeError):
    pass


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: str


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _column_names(payload: dict[str, Any]) -> list[str]:
    props = payload.get("properties") or {}
    return [str(c.get("name")) for c in props.get("columns", [])]


def rows_as_dicts(payload: dict[str, Any]) -> list[dict[str, Any]]:
    props = payload.get("properties") or {}
    names = _column_names(payload)
    rows = props.get("rows", [])
    if not names:
        raise CostReportError("Cost Management response has no columns")
    result = []
    for row in rows:
        if len(row) != len(names):
            raise CostReportError("Cost Management row/column length mismatch")
        result.append(dict(zip(names, row)))
    return result


def _money(value: Any) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError) as exc:
        raise CostReportError(f"Invalid cost value: {value!r}") from exc


def _usage_date(value: Any) -> date:
    text = str(value)
    if text.endswith(".0"):
        text = text[:-2]
    if len(text) != 8 or not text.isdigit():
        raise CostReportError(f"Unexpected UsageDate: {value!r}")
    return datetime.strptime(text, "%Y%m%d").date()


def _row_cost(row: Mapping[str, Any]) -> float:
    for key in ("PreTaxCost", "Cost", "totalCost"):
        if key in row:
            return _money(row[key])
    raise CostReportError("Cost Management response does not contain PreTaxCost/Cost/totalCost")


def summarize_daily(payload: dict[str, Any]) -> tuple[list[tuple[date, float]], str]:
    totals: dict[date, float] = defaultdict(float)
    currency = "USD"
    for row in rows_as_dicts(payload):
        day = _usage_date(row["UsageDate"])
        totals[day] += _row_cost(row)
        if row.get("Currency"):
            currency = str(row["Currency"])
    return sorted(totals.items()), currency


def summarize_services(payload: dict[str, Any], limit: int = 12) -> list[dict[str, Any]]:
    totals: dict[str, float] = defaultdict(float)
    for row in rows_as_dicts(payload):
        totals[str(row.get("ServiceName") or "Unknown")] += _row_cost(row)
    ordered = sorted(totals.items(), key=lambda kv: kv[1], reverse=True)
    return [{"service": name, "cost": round(cost, 4)} for name, cost in ordered[:limit]]


def compute_forecast(
    daily: Iterable[tuple[date, float]], *, budget: float, month: date
) -> dict[str, Any]:
    daily = list(daily)
    days_in_month = calendar.monthrange(month.year, month.month)[1]
    if not daily:
        return {
            "mtd_actual": 0.0,
            "latest_cost_date": None,
            "data_lag_days": None,
            "linear_forecast": 0.0,
            "recent7_forecast": 0.0,
            "forecast": 0.0,
            "budget": budget,
            "projected_buffer": budget,
            "budget_utilization_pct": 0.0,
            "forecast_utilization_pct": 0.0,
            "risk": "GREEN",
        }

    total = sum(cost for _, cost in daily)
    latest = max(day for day, _ in daily)
    first_of_month = date(month.year, month.month, 1)
    if latest < first_of_month or latest.month != month.month or latest.year != month.year:
        raise CostReportError("Latest cost row is outside the requested month")

    linear = total / max(latest.day, 1) * days_in_month
    by_day = dict(daily)
    recent_start = max(1, latest.day - 6)
    recent_values = [
        by_day.get(date(month.year, month.month, d), 0.0)
        for d in range(recent_start, latest.day + 1)
    ]
    recent_avg = sum(recent_values) / max(len(recent_values), 1)
    recent7 = total + recent_avg * max(days_in_month - latest.day, 0)
    forecast = max(linear, recent7)
    forecast_pct = 100.0 * forecast / budget if budget > 0 else math.inf
    actual_pct = 100.0 * total / budget if budget > 0 else math.inf
    risk = "RED" if forecast_pct >= 100 else "ORANGE" if forecast_pct >= 95 else "YELLOW" if forecast_pct >= 80 else "GREEN"
    lag = max((_utc_now().date() - latest).days, 0)
    return {
        "mtd_actual": round(total, 4),
        "latest_cost_date": latest.isoformat(),
        "data_lag_days": lag,
        "linear_forecast": round(linear, 4),
        "recent7_forecast": round(recent7, 4),
        "forecast": round(forecast, 4),
        "budget": round(budget, 2),
        "projected_buffer": round(budget - forecast, 4),
        "budget_utilization_pct": round(actual_pct, 2),
        "forecast_utilization_pct": round(forecast_pct, 2),
        "risk": risk,
    }


def build_query_body() -> dict[str, Any]:
    return {
        "type": "ActualCost",
        "timeframe": "MonthToDate",
        "dataset": {
            "granularity": "Daily",
            "aggregation": {"totalCost": {"name": "PreTaxCost", "function": "Sum"}},
            "grouping": [{"type": "Dimension", "name": "ServiceName"}],
        },
    }


def _normalized_headers(headers: Mapping[str, str]) -> dict[str, str]:
    return {str(k).lower(): str(v).strip() for k, v in headers.items()}


def _parse_retry_value(value: str, *, now: datetime) -> int | None:
    text = value.strip()
    try:
        numeric = float(text)
        if numeric < 0:
            return None
        return int(math.ceil(numeric))
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=timezone.utc)
    return max(0, int(math.ceil((target - now).total_seconds())))


def retry_delay_from_headers(
    headers: Mapping[str, str], *, now: datetime | None = None
) -> tuple[int | None, str | None]:
    normalized = _normalized_headers(headers)
    now = now or _utc_now()
    for key in (QPU_RETRY_HEADER, CONSUMPTION_RETRY_HEADER, STANDARD_RETRY_HEADER):
        if key in normalized:
            delay = _parse_retry_value(normalized[key], now=now)
            if delay is not None:
                return delay, key
    return None, None


def _safe_error_body(body: str) -> str:
    return (body or "").strip().replace("\n", " ")[:600]


def _rate_limit_meta(headers: Mapping[str, str]) -> dict[str, Any]:
    h = _normalized_headers(headers)
    return {
        "qpu_consumed": h.get("x-ms-ratelimit-microsoft.costmanagement-qpu-consumed"),
        "qpu_remaining": h.get("x-ms-ratelimit-microsoft.costmanagement-qpu-remaining"),
        "qpu_retry_after": h.get(QPU_RETRY_HEADER),
        "consumption_retry_after": h.get(CONSUMPTION_RETRY_HEADER),
        "retry_after": h.get(STANDARD_RETRY_HEADER),
    }


def query_cost_payload(
    subscription_id: str,
    *,
    transport: Callable[[str, dict[str, Any], str], HttpResult],
    access_token_fn: Callable[[], str],
    sleep_fn: Callable[[float], None] = time.sleep,
    clock_fn: Callable[[], datetime] = _utc_now,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    max_total_wait_seconds: int = DEFAULT_MAX_TOTAL_WAIT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    if max_attempts < 1:
        raise ValueError("max_attempts must be >= 1")
    if max_total_wait_seconds < 0:
        raise ValueError("max_total_wait_seconds must be >= 0")
    token = access_token_fn()
    url = (
        f"https://management.azure.com/subscriptions/{subscription_id}"
        f"/providers/Microsoft.CostManagement/query?api-version={COST_API_VERSION}"
    )
    body = build_query_body()
    total_wait = 0
    throttle_events = 0
    retry_history: list[dict[str, Any]] = []
    for attempt in range(1, max_attempts + 1):
        result = transport(url, body, token)
        if 200 <= result.status < 300:
            try:
                payload = json.loads(result.body)
            except json.JSONDecodeError as exc:
                raise CostReportError("Azure Cost Management returned invalid JSON") from exc
            completed_at = clock_fn().astimezone(timezone.utc)
            return payload, {
                "attempts": attempt,
                "throttle_events": throttle_events,
                "total_throttle_wait_seconds": total_wait,
                "retry_history": retry_history,
                "rate_limit": _rate_limit_meta(result.headers),
                "completed_at": completed_at.isoformat(),
            }
        if result.status not in TRANSIENT_HTTP_STATUSES:
            raise CostReportError(f"Azure Cost Management HTTP {result.status}: {_safe_error_body(result.body)}")
        throttle_events += 1
        if attempt >= max_attempts:
            raise CostReportError(
                f"Azure Cost Management HTTP {result.status} after {attempt} attempts: {_safe_error_body(result.body)}"
            )
        server_delay, source = retry_delay_from_headers(result.headers)
        fallback_index = min(attempt - 1, len(FALLBACK_RETRY_DELAYS) - 1)
        delay = server_delay if server_delay is not None else FALLBACK_RETRY_DELAYS[fallback_index]
        remaining = max_total_wait_seconds - total_wait
        if delay > remaining:
            raise CostReportError(
                f"Azure Cost Management HTTP {result.status}: retry wait {delay}s exceeds remaining wait budget {remaining}s"
            )
        rate = _rate_limit_meta(result.headers)
        retry_history.append({
            "status": result.status,
            "attempt": attempt,
            "delay_seconds": delay,
            "delay_source": source or "fallback",
            "qpu_remaining": rate.get("qpu_remaining"),
        })
        sleep_fn(delay)
        total_wait += delay
    raise CostReportError("Azure Cost retry loop exhausted")


def build_live_report(
    subscription_id: str,
    budget: float,
    payload: dict[str, Any],
    *,
    now: datetime | None = None,
    query_meta: dict[str, Any] | None = None,
) -> dict[str, Any]:
    now = now or _utc_now()
    daily, currency = summarize_daily(payload)
    return {
        "schema": "azure_cost_report_v2",
        "generated_at": now.isoformat(),
        "azure_queried_at": now.isoformat(),
        "served_at": now.isoformat(),
        "subscription_id": subscription_id,
        "currency": currency,
        **compute_forecast(daily, budget=budget, month=now.date()),
        "top_services": summarize_services(payload),
        "daily": [{"date": day.isoformat(), "cost": round(cost, 4)} for day, cost in daily],
        "forecast_method": "max(month-to-date linear pace, trailing-7-calendar-day pace)",
        "retrieval_mode": "AZURE_LIVE",
        "cache_age_hours": 0.0,
        "query_meta": query_meta or {},
    }


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def cache_status(
    report: dict[str, Any] | None,
    subscription_id: str,
    now: datetime,
    max_age_hours: float,
) -> dict[str, Any]:
    if not isinstance(report, dict):
        return {"usable": False, "fresh": False, "age_hours": None, "reason": "missing"}
    if report.get("subscription_id") != subscription_id:
        return {"usable": False, "fresh": False, "age_hours": None, "reason": "subscription_mismatch"}
    queried = _parse_iso_datetime(report.get("azure_queried_at") or report.get("generated_at"))
    if queried is None:
        return {"usable": False, "fresh": False, "age_hours": None, "reason": "missing_query_timestamp"}
    now_utc = now.astimezone(timezone.utc) if now.tzinfo else now.replace(tzinfo=timezone.utc)
    age_hours = max((now_utc - queried).total_seconds() / 3600.0, 0.0)
    return {
        "usable": True,
        "fresh": age_hours <= max_age_hours,
        "age_hours": round(age_hours, 6),
        "reason": "fresh" if age_hours <= max_age_hours else "stale",
    }


def _cached_response(
    report: dict[str, Any], *, now: datetime, mode: str, age_hours: float, warning: str | None = None
) -> dict[str, Any]:
    result = dict(report)
    result["served_at"] = now.isoformat()
    result["retrieval_mode"] = mode
    result["cache_age_hours"] = round(age_hours, 6)
    if warning:
        result["refresh_warning"] = warning[:600]
    else:
        result.pop("refresh_warning", None)
    return result


def resolve_report(
    subscription_id: str,
    budget: float,
    *,
    cached_report: dict[str, Any] | None = None,
    now: datetime | None = None,
    max_cache_age_hours: float = DEFAULT_MAX_CACHE_AGE_HOURS,
    force_refresh: bool = False,
    query_fn: Callable[[str], tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    now = now or _utc_now()
    status = cache_status(cached_report, subscription_id, now, max_cache_age_hours)
    if status["usable"] and status["fresh"] and not force_refresh:
        return _cached_response(cached_report or {}, now=now, mode="CACHE", age_hours=float(status["age_hours"]))
    try:
        payload, query_meta = query_fn(subscription_id)
        completed = _parse_iso_datetime(query_meta.get("completed_at")) or now
        return build_live_report(subscription_id, budget, payload, now=completed, query_meta=query_meta)
    except CostReportError as exc:
        if status["usable"] and cached_report is not None:
            return _cached_response(
                cached_report,
                now=now,
                mode="STALE_CACHE_FALLBACK",
                age_hours=float(status["age_hours"]),
                warning=str(exc),
            )
        raise


__all__ = [
    "COST_API_VERSION",
    "DEFAULT_MAX_CACHE_AGE_HOURS",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_MAX_TOTAL_WAIT_SECONDS",
    "FALLBACK_RETRY_DELAYS",
    "TRANSIENT_HTTP_STATUSES",
    "QPU_RETRY_HEADER",
    "CONSUMPTION_RETRY_HEADER",
    "STANDARD_RETRY_HEADER",
    "CostReportError",
    "HttpResult",
    "rows_as_dicts",
    "summarize_daily",
    "summarize_services",
    "compute_forecast",
    "build_query_body",
    "retry_delay_from_headers",
    "query_cost_payload",
    "build_live_report",
    "cache_status",
    "resolve_report",
]
