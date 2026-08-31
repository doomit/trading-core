from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse


REQUIRED_FIELDS = {
    "schema",
    "news_id",
    "provider",
    "source_url",
    "headline",
    "published_at",
    "observed_at",
    "retrieved_at",
    "affected_symbols",
    "themes",
    "confidence",
    "relevance",
    "status",
    "summary",
}
ALLOWED_STATUS = {"fresh", "stale", "contradicted"}
FRESH_MAX_AGE_SECONDS = 120 * 60


def _parse_iso(value, field, errors):
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{field} must be a non-empty ISO timestamp")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        errors.append(f"{field} must be a valid ISO timestamp")
        return None
    if parsed.tzinfo is None:
        errors.append(f"{field} must include a timezone")
        return None
    return parsed.astimezone(timezone.utc)


def validate_news_context(item, now_iso):
    errors = []
    if not isinstance(item, dict):
        return ["item must be an object"]

    fields = set(item)
    for field in sorted(REQUIRED_FIELDS - fields):
        errors.append(f"missing required field: {field}")
    for field in sorted(fields - REQUIRED_FIELDS):
        errors.append(f"unexpected field: {field}")
    if errors:
        return errors

    if item["schema"] != "market_news_v1":
        errors.append("schema must be market_news_v1")

    for field in ("news_id", "provider", "headline", "summary"):
        if not isinstance(item[field], str) or not item[field].strip():
            errors.append(f"{field} must be a non-empty string")

    source_url = item["source_url"]
    parsed_source_url = urlparse(source_url) if isinstance(source_url, str) else None
    if (
        parsed_source_url is None
        or parsed_source_url.scheme not in {"http", "https"}
        or not parsed_source_url.netloc
        or not source_url.startswith(("http://", "https://"))
    ):
        errors.append("source_url must be an absolute http(s) URL")

    for field in ("affected_symbols", "themes"):
        value = item[field]
        if not isinstance(value, list) or not all(isinstance(entry, str) and entry.strip() for entry in value):
            errors.append(f"{field} must be an array of non-empty strings")

    for field in ("confidence", "relevance"):
        value = item[field]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 1:
            errors.append(f"{field} must be within [0,1]")

    if item["status"] not in ALLOWED_STATUS:
        errors.append(f"status must be one of {sorted(ALLOWED_STATUS)}")

    published = _parse_iso(item["published_at"], "published_at", errors)
    observed = _parse_iso(item["observed_at"], "observed_at", errors)
    retrieved = _parse_iso(item["retrieved_at"], "retrieved_at", errors)
    now = _parse_iso(now_iso, "now", errors)

    if published and observed and observed < published:
        errors.append("observed_at must not precede published_at")
    if published and retrieved and retrieved < published:
        errors.append("retrieved_at must not precede published_at")
    if observed and retrieved and retrieved < observed:
        errors.append("retrieved_at must not precede observed_at")
    if retrieved and now and retrieved > now:
        errors.append("retrieved_at must not be in the future")
    if published and now and item["status"] == "fresh":
        age_seconds = (now - published).total_seconds()
        if age_seconds < 0 or age_seconds > FRESH_MAX_AGE_SECONDS:
            errors.append("status fresh requires publication within the 120-minute freshness window")

    return errors
