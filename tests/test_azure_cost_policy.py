from datetime import date, datetime, timedelta, timezone

import pytest

from trading_core.azure_cost_policy import (
    DEFAULT_MAX_CACHE_AGE_HOURS,
    CostReportError,
    HttpResult,
    build_live_report,
    build_query_body,
    cache_status,
    compute_forecast,
    query_cost_payload,
    resolve_report,
    retry_delay_from_headers,
    rows_as_dicts,
    summarize_daily,
    summarize_services,
)


def payload(columns, rows):
    return {"properties": {"columns": [{"name": c} for c in columns], "rows": rows}}


def test_rows_as_dicts_validates_shape():
    with pytest.raises(CostReportError):
        rows_as_dicts(payload(["a", "b"], [[1]]))


def test_daily_service_summary_and_forecast():
    p = payload(
        ["PreTaxCost", "ServiceName", "UsageDate", "Currency"],
        [
            [3.5, "Storage", 20260801, "USD"],
            [1.25, "Functions", 20260801, "USD"],
            [2.0, "Storage", 20260802, "USD"],
        ],
    )
    daily, currency = summarize_daily(p)
    assert daily == [(date(2026, 8, 1), 4.75), (date(2026, 8, 2), 2.0)]
    assert currency == "USD"
    assert summarize_services(p)[:2] == [
        {"service": "Storage", "cost": 5.5},
        {"service": "Functions", "cost": 1.25},
    ]
    result = compute_forecast(daily, budget=150, month=date(2026, 8, 29))
    assert result["mtd_actual"] == 6.75
    assert result["forecast"] >= result["linear_forecast"]


def test_forecast_uses_latest_cost_day_and_recent_spike_conservatively():
    steady = [(date(2026, 8, d), 5.0) for d in range(1, 11)]
    r = compute_forecast(steady, budget=150, month=date(2026, 8, 29))
    assert r["linear_forecast"] == 155.0
    assert r["forecast"] == 155.0
    assert r["risk"] == "RED"

    spiky = [(date(2026, 8, d), 1.0) for d in range(1, 15)]
    spiky += [(date(2026, 8, d), 10.0) for d in range(15, 21)]
    r2 = compute_forecast(spiky, budget=150, month=date(2026, 8, 29))
    assert r2["recent7_forecast"] > r2["linear_forecast"]
    assert r2["forecast"] == r2["recent7_forecast"]


def test_query_body_and_retry_header_priority():
    body = build_query_body()
    assert body["type"] == "ActualCost"
    assert body["timeframe"] == "MonthToDate"
    assert body["dataset"]["granularity"] == "Daily"
    assert body["dataset"]["grouping"] == [{"type": "Dimension", "name": "ServiceName"}]

    delay, source = retry_delay_from_headers({
        "Retry-After": "3",
        "x-ms-ratelimit-microsoft.consumption-retry-after": "7",
        "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after": "41",
    })
    assert (delay, source) == (
        41,
        "x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after",
    )


def test_retry_after_http_date_is_supported():
    now = datetime(2026, 8, 29, 17, 0, 0, tzinfo=timezone.utc)
    assert retry_delay_from_headers(
        {"Retry-After": "Sat, 29 Aug 2026 17:00:37 GMT"}, now=now
    ) == (37, "retry-after")


def test_query_retries_transient_response_then_succeeds():
    success = payload(
        ["PreTaxCost", "ServiceName", "UsageDate", "Currency"],
        [[1.0, "Storage", 20260829, "USD"]],
    )
    responses = [
        HttpResult(
            status=429,
            headers={"x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after": "23"},
            body='{"error":{"code":"429"}}',
        ),
        HttpResult(status=200, headers={}, body='{"properties":{"columns":[{"name":"PreTaxCost"},{"name":"ServiceName"},{"name":"UsageDate"},{"name":"Currency"}],"rows":[[1.0,"Storage",20260829,"USD"]]}}'),
    ]
    sleeps = []

    got, meta = query_cost_payload(
        "sub",
        transport=lambda url, body, token: responses.pop(0),
        access_token_fn=lambda: "token",
        sleep_fn=sleeps.append,
        max_attempts=3,
        max_total_wait_seconds=100,
    )
    assert got == success
    assert sleeps == [23]
    assert meta["attempts"] == 2
    assert meta["throttle_events"] == 1


def test_query_fails_closed_for_non_transient_and_excessive_wait():
    with pytest.raises(CostReportError, match="HTTP 403"):
        query_cost_payload(
            "sub",
            transport=lambda *_: HttpResult(403, {}, '{"error":{"code":"Forbidden"}}'),
            access_token_fn=lambda: "token",
            sleep_fn=lambda _: None,
        )

    with pytest.raises(CostReportError, match="retry wait 601s exceeds remaining wait budget"):
        query_cost_payload(
            "sub",
            transport=lambda *_: HttpResult(
                429,
                {"x-ms-ratelimit-microsoft.costmanagement-qpu-retry-after": "601"},
                '{"error":{"code":"429"}}',
            ),
            access_token_fn=lambda: "token",
            sleep_fn=lambda _: None,
            max_total_wait_seconds=600,
        )


def cached_report(queried_at, subscription_id="sub"):
    return {
        "schema": "azure_cost_report_v2",
        "subscription_id": subscription_id,
        "azure_queried_at": queried_at.isoformat(),
        "generated_at": queried_at.isoformat(),
        "currency": "USD",
        "mtd_actual": 50.0,
        "forecast": 100.0,
        "budget": 150.0,
        "projected_buffer": 50.0,
        "forecast_utilization_pct": 66.67,
        "risk": "GREEN",
        "latest_cost_date": "2026-08-28",
        "data_lag_days": 1,
        "top_services": [],
        "daily": [],
    }


def test_cache_policy_and_stale_fallback():
    assert DEFAULT_MAX_CACHE_AGE_HOURS == 23.0
    now = datetime(2026, 8, 29, 17, 0, tzinfo=timezone.utc)
    fresh = cache_status(cached_report(now - timedelta(hours=3)), "sub", now, 4)
    stale = cache_status(cached_report(now - timedelta(hours=8)), "sub", now, 4)
    other = cache_status(cached_report(now, "other"), "sub", now, 4)
    assert fresh["fresh"] is True
    assert stale["usable"] is True and stale["fresh"] is False
    assert other["usable"] is False

    report = resolve_report(
        "sub",
        150,
        cached_report=cached_report(now - timedelta(hours=8)),
        now=now,
        max_cache_age_hours=4,
        query_fn=lambda _: (_ for _ in ()).throw(CostReportError("HTTP 429 throttled")),
    )
    assert report["retrieval_mode"] == "STALE_CACHE_FALLBACK"
    assert "429" in report["refresh_warning"]


def test_live_report_reuses_one_payload_for_daily_and_services():
    now = datetime(2026, 8, 29, 17, 0, tzinfo=timezone.utc)
    p = payload(
        ["PreTaxCost", "ServiceName", "UsageDate", "Currency"],
        [
            [3.0, "Storage", 20260828, "USD"],
            [2.0, "Functions", 20260828, "USD"],
            [1.0, "Storage", 20260829, "USD"],
        ],
    )
    report = build_live_report("sub", 150, p, now=now, query_meta={"attempts": 1})
    assert report["mtd_actual"] == 6.0
    assert report["daily"] == [
        {"date": "2026-08-28", "cost": 5.0},
        {"date": "2026-08-29", "cost": 1.0},
    ]
    assert report["top_services"][:2] == [
        {"service": "Storage", "cost": 4.0},
        {"service": "Functions", "cost": 2.0},
    ]
