from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Optional, Protocol, Sequence

SERVICE_VERSION = "3.2.0"
PRODUCER_VERSION = f"ingestion_v{SERVICE_VERSION}"
INGEST_SCHEMA_VERSION = "market_ingest_v2"
UPLOAD_SCHEMA_VERSION = "market_upload_v1"
RAW_SCHEMA_VERSION = "rawbars_v2"
CANONICAL_SCHEMA_VERSION = "canonical5m_v2"
CANONICAL_LOGIC_VERSION = "canonical5m_v2"

MAX_BARS_PER_PAYLOAD = 20
MAX_HTTP_BODY_BYTES = 65_536
MAX_SOURCE_LENGTH = 64
MAX_DATASET_ID_LENGTH = 128
MAX_DATASET_VERSION_LENGTH = 32
MAX_RUN_ID_LENGTH = 128
MAX_REASON_LENGTH = 256
SYMBOL_ROOT = {"MES1!": "MES", "MNQ1!": "MNQ"}
TIMEFRAME_MS = {"1": 60_000, "5": 300_000}
QUALITY_RANK = {"NATIVE_5M": 1, "FULL_1M": 2}

ENV_PROD = "PROD"
ENV_TEST = "TEST"
DATA_CLASS_REAL = "REAL"
DATA_CLASS_TEST = "TEST"

ORIGIN_TRADINGVIEW_WEBHOOK = "TRADINGVIEW_WEBHOOK"
ORIGIN_CSV_UPLOAD = "CSV_UPLOAD"
ORIGIN_INTEGRATION_TEST = "INTEGRATION_TEST"
ORIGIN_TEST_UPLOAD = "TEST_UPLOAD"
ORIGIN_DERIVED_1M = "DERIVED_1M"
ORIGIN_NATIVE_5M = "NATIVE_5M_INPUT"

MODE_LIVE_WEBHOOK = "LIVE_WEBHOOK"
MODE_BATCH_UPLOAD = "BATCH_UPLOAD"
MODE_TEST_WEBHOOK = "TEST_WEBHOOK"
MODE_TEST_UPLOAD = "TEST_UPLOAD"
MODE_DERIVED = "DERIVED"

CORRECTION_IMMUTABLE = "IMMUTABLE"
CORRECTION_SAFE = "SAFE"
CORRECTION_RECONCILE = "RECONCILE"
CORRECTION_MODES = {CORRECTION_IMMUTABLE, CORRECTION_SAFE, CORRECTION_RECONCILE}


class Repository(Protocol):
    def upsert_raw(self, entity: dict, *, correction_mode: str, request_id: str, received_at: int) -> dict: ...
    def get_raw_bar(self, *, symbol: str, timeframe: str, t: int) -> Optional[dict]: ...
    def put_canonical(self, entity: dict) -> dict: ...
    def upsert_status(self, entity: dict) -> None: ...


def month_key(epoch_ms: int) -> str:
    dt = datetime.fromtimestamp(epoch_ms / 1000.0, tz=timezone.utc)
    return f"{dt.year:04d}{dt.month:02d}"


def safe_series_id(symbol: str) -> str:
    return "".join(ch for ch in symbol if ch.isalnum()).upper()


def safe_key(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in str(value))


def tf_label(timeframe: str) -> str:
    if timeframe == "1":
        return "1m"
    if timeframe == "5":
        return "5m"
    raise ValueError(f"unsupported timeframe: {timeframe}")


def raw_partition(symbol: str, timeframe: str, epoch_ms: int) -> str:
    return f"{safe_series_id(symbol)}_{tf_label(timeframe)}_{month_key(epoch_ms)}"


def canonical_partition(root: str, epoch_ms: int) -> str:
    return f"{root.upper()}_5m_{month_key(epoch_ms)}"


def row_key(epoch_ms: int) -> str:
    return f"{int(epoch_ms):013d}"


def bucket_start_5m(epoch_ms: int) -> int:
    return (int(epoch_ms) // 300_000) * 300_000


def _finite_number(value) -> bool:
    if isinstance(value, bool):
        return False
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError, OverflowError):
        return False


def _exact_int(value):
    if isinstance(value, bool):
        raise ValueError("bool is not accepted as integer data")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value) or not value.is_integer():
            raise ValueError("non-integral float")
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            raise ValueError("empty integer string")
        body = text[1:] if text[0] in "+-" else text
        if not body.isdigit():
            raise ValueError("non-integer string")
        return int(text)
    raise ValueError("unsupported integer type")


def _bounded_text(value, *, max_len: int, allow_none: bool = False) -> bool:
    if value is None:
        return allow_none
    return isinstance(value, str) and 0 < len(value) <= max_len


def validate_payload(payload: dict):
    if not isinstance(payload, dict):
        return "payload_not_object", {}
    if payload.get("schema") != "tv_bars_v2":
        return "invalid_schema", {}

    symbol = payload.get("symbol")
    if symbol not in SYMBOL_ROOT:
        return "invalid_symbol", {"symbol": symbol}

    root = payload.get("root")
    if root != SYMBOL_ROOT[symbol]:
        return "symbol_root_mismatch", {"symbol": symbol, "root": root}

    timeframe = str(payload.get("timeframe"))
    if timeframe not in TIMEFRAME_MS:
        return "invalid_timeframe", {"timeframe": timeframe}

    source = payload.get("source")
    if source is not None:
        if not isinstance(source, str) or not source or len(source) > MAX_SOURCE_LENGTH:
            return "invalid_source", {}

    bars = payload.get("bars")
    if not isinstance(bars, list):
        return "bars_not_array", {}
    if not (1 <= len(bars) <= MAX_BARS_PER_PAYLOAD):
        return "invalid_bar_count", {"bar_count": len(bars)}

    window = payload.get("window")
    if window is not None:
        try:
            window_int = _exact_int(window)
        except (TypeError, ValueError, OverflowError):
            return "invalid_window", {"window": window}
        if window_int != len(bars):
            return "window_bar_count_mismatch", {"window": window_int, "bar_count": len(bars)}

    sent_at = payload.get("sent_at")
    if sent_at is not None:
        try:
            sent_at_int = _exact_int(sent_at)
        except (TypeError, ValueError, OverflowError):
            return "invalid_sent_at", {"sent_at": sent_at}
        if sent_at_int < 0:
            return "invalid_sent_at", {"sent_at": sent_at}

    duration = TIMEFRAME_MS[timeframe]
    required = {"t", "tc", "o", "h", "l", "c", "v"}
    previous_t = None

    for index, bar in enumerate(bars):
        if not isinstance(bar, dict):
            return "bar_not_object", {"bar_index": index}
        missing = sorted(required.difference(bar.keys()))
        if missing:
            return "bar_missing_fields", {"bar_index": index, "missing": missing}

        try:
            t = _exact_int(bar["t"])
            tc = _exact_int(bar["tc"])
        except (TypeError, ValueError, OverflowError):
            return "invalid_bar_timestamp", {"bar_index": index}

        if t < 0 or tc < 0:
            return "negative_bar_timestamp", {"bar_index": index}
        if t % duration != 0:
            return "bar_start_not_aligned", {"bar_index": index, "t": t}
        if tc != t + duration:
            return "unexpected_bar_duration", {
                "bar_index": index,
                "duration_ms": tc - t,
                "expected_ms": duration,
            }
        if previous_t is not None and t <= previous_t:
            return "bars_not_strictly_ascending", {"bar_index": index}
        previous_t = t

        if not all(_finite_number(bar[k]) for k in ("o", "h", "l", "c")):
            return "invalid_ohlc", {"bar_index": index}
        o, h, l, c = (float(bar[k]) for k in ("o", "h", "l", "c"))
        if h < l or h < max(o, c) or l > min(o, c):
            return "invalid_ohlc_invariants", {"bar_index": index}

        try:
            v = _exact_int(bar["v"])
        except (TypeError, ValueError, OverflowError):
            return "invalid_volume", {"bar_index": index}
        if v < 0:
            return "negative_volume", {"bar_index": index}

    return None, {}


def validate_upload_request(body: dict):
    if not isinstance(body, dict):
        return "upload_not_object", {}
    if body.get("schema") != UPLOAD_SCHEMA_VERSION:
        return "invalid_upload_schema", {}
    if not _bounded_text(body.get("dataset_id"), max_len=MAX_DATASET_ID_LENGTH):
        return "invalid_dataset_id", {}
    if not _bounded_text(body.get("dataset_version"), max_len=MAX_DATASET_VERSION_LENGTH):
        return "invalid_dataset_version", {}
    if not _bounded_text(body.get("run_id"), max_len=MAX_RUN_ID_LENGTH):
        return "invalid_run_id", {}
    mode = body.get("correction_mode", CORRECTION_RECONCILE)
    if mode not in (CORRECTION_SAFE, CORRECTION_RECONCILE):
        return "invalid_correction_mode", {"correction_mode": mode}
    reason = body.get("correction_reason")
    if reason is not None and (not isinstance(reason, str) or len(reason) > MAX_REASON_LENGTH):
        return "invalid_correction_reason", {}
    error, details = validate_payload(body.get("payload"))
    if error:
        return "invalid_upload_payload", {"payload_error": error, **details}
    return None, {}


def profile_for_live(*, received_at: int) -> dict:
    return {
        "environment": ENV_PROD,
        "data_class": DATA_CLASS_REAL,
        "data_origin": ORIGIN_TRADINGVIEW_WEBHOOK,
        "ingest_mode": MODE_LIVE_WEBHOOK,
        "dataset_id": "tradingview_live_continuous",
        "dataset_version": "continuous",
        "run_id": f"live_{month_key(received_at)}",
        "correction_mode": CORRECTION_IMMUTABLE,
        "correction_reason": "",
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
    }


def profile_for_test_live(*, request_id: str) -> dict:
    return {
        "environment": ENV_TEST,
        "data_class": DATA_CLASS_TEST,
        "data_origin": ORIGIN_INTEGRATION_TEST,
        "ingest_mode": MODE_TEST_WEBHOOK,
        "dataset_id": "integration_test",
        "dataset_version": SERVICE_VERSION,
        "run_id": f"test_{request_id[:24]}",
        "correction_mode": CORRECTION_IMMUTABLE,
        "correction_reason": "",
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
    }


def profile_for_upload(body: dict, *, test: bool = False) -> dict:
    return {
        "environment": ENV_TEST if test else ENV_PROD,
        "data_class": DATA_CLASS_TEST if test else DATA_CLASS_REAL,
        "data_origin": ORIGIN_TEST_UPLOAD if test else ORIGIN_CSV_UPLOAD,
        "ingest_mode": MODE_TEST_UPLOAD if test else MODE_BATCH_UPLOAD,
        "dataset_id": str(body["dataset_id"]),
        "dataset_version": str(body["dataset_version"]),
        "run_id": str(body["run_id"]),
        "correction_mode": str(body.get("correction_mode", CORRECTION_RECONCILE)),
        "correction_reason": str(body.get("correction_reason") or ""),
        "raw_schema_version": RAW_SCHEMA_VERSION,
        "producer_version": PRODUCER_VERSION,
    }


def validate_profile(profile: dict):
    if not isinstance(profile, dict):
        return "profile_not_object", {}
    env = profile.get("environment")
    data_class = profile.get("data_class")
    if env not in (ENV_PROD, ENV_TEST):
        return "invalid_environment", {}
    if data_class not in (DATA_CLASS_REAL, DATA_CLASS_TEST):
        return "invalid_data_class", {}
    if (env == ENV_PROD) != (data_class == DATA_CLASS_REAL):
        return "environment_data_class_mismatch", {}
    if not _bounded_text(profile.get("data_origin"), max_len=64):
        return "invalid_data_origin", {}
    if not _bounded_text(profile.get("ingest_mode"), max_len=64):
        return "invalid_ingest_mode", {}
    if not _bounded_text(profile.get("dataset_id"), max_len=MAX_DATASET_ID_LENGTH):
        return "invalid_dataset_id", {}
    if not _bounded_text(profile.get("dataset_version"), max_len=MAX_DATASET_VERSION_LENGTH):
        return "invalid_dataset_version", {}
    if not _bounded_text(profile.get("run_id"), max_len=MAX_RUN_ID_LENGTH):
        return "invalid_run_id", {}
    if profile.get("correction_mode") not in CORRECTION_MODES:
        return "invalid_correction_mode", {}
    if profile.get("raw_schema_version") != RAW_SCHEMA_VERSION:
        return "invalid_raw_schema_version", {}
    if profile.get("producer_version") != PRODUCER_VERSION:
        return "invalid_producer_version", {}
    return None, {}


def make_ingest_envelope(*, payload: dict, profile: dict, request_id: str, received_at: int, transport_ms=None) -> dict:
    return {
        "ingest_schema": INGEST_SCHEMA_VERSION,
        "request_id": request_id,
        "server_received_at": int(received_at),
        "transport_ms": transport_ms,
        "profile": dict(profile),
        "payload": payload,
    }


def validate_ingest_envelope(envelope: dict):
    if not isinstance(envelope, dict):
        return "envelope_not_object", {}
    if envelope.get("ingest_schema") != INGEST_SCHEMA_VERSION:
        return "invalid_ingest_schema", {}
    request_id = envelope.get("request_id")
    if not isinstance(request_id, str) or not request_id or len(request_id) > 128:
        return "invalid_request_id", {}
    try:
        received_at = _exact_int(envelope.get("server_received_at"))
    except (TypeError, ValueError, OverflowError):
        return "invalid_server_received_at", {}
    if received_at < 0:
        return "invalid_server_received_at", {}
    error, details = validate_profile(envelope.get("profile"))
    if error:
        return "invalid_envelope_profile", {"profile_error": error, **details}
    error, details = validate_payload(envelope.get("payload"))
    if error:
        return "invalid_envelope_payload", {"payload_error": error, **details}
    return None, {}


def bar_hash_from_values(*, t: int, tc: int, o: float, h: float, l: float, c: float, v: int) -> str:
    payload = [int(t), int(tc), float(o), float(h), float(l), float(c), int(v)]
    return hashlib.sha256(json.dumps(payload, separators=(",", ":")).encode()).hexdigest()


def bar_hash_from_entity(entity: dict) -> str:
    existing = entity.get("BarHash")
    if isinstance(existing, str) and existing:
        return existing
    return bar_hash_from_values(
        t=int(entity["BarStart"]),
        tc=int(entity["BarClose"]),
        o=float(entity["Open"]),
        h=float(entity["High"]),
        l=float(entity["Low"]),
        c=float(entity["Close"]),
        v=int(entity["Volume"]),
    )


def raw_entity(*, payload: dict, bar: dict, profile: dict, request_id: str, received_at: int) -> dict:
    symbol = str(payload["symbol"])
    root = str(payload["root"])
    timeframe = str(payload["timeframe"])
    t = int(bar["t"])
    entity = {
        "PartitionKey": raw_partition(symbol, timeframe, t),
        "RowKey": row_key(t),
        "SeriesId": safe_series_id(symbol),
        "Symbol": symbol,
        "Root": root,
        "Timeframe": tf_label(timeframe),
        "BarStart": t,
        "BarClose": int(bar["tc"]),
        "Open": float(bar["o"]),
        "High": float(bar["h"]),
        "Low": float(bar["l"]),
        "Close": float(bar["c"]),
        "Volume": int(bar["v"]),
        "Source": str(payload.get("source", "unknown")),
        "SourceType": "native_1m" if timeframe == "1" else "native_5m",
        "RequestId": request_id,
        "CorrectionReason": profile.get("correction_reason", ""),
        "Environment": profile["environment"],
        "DataClass": profile["data_class"],
        "DataOrigin": profile["data_origin"],
        "IngestMode": profile["ingest_mode"],
        "DatasetId": profile["dataset_id"],
        "DatasetVersion": profile["dataset_version"],
        "RunId": profile["run_id"],
        "SchemaVersion": RAW_SCHEMA_VERSION,
        "ProducerVersion": PRODUCER_VERSION,
        "Revision": 1,
        "CorrectionCount": 0,
        "FirstIngestedAt": int(received_at),
        "LastIngestedAt": int(received_at),
        "LastModifiedAt": int(received_at),
        "IngestedAt": int(received_at),
    }
    entity["BarHash"] = bar_hash_from_entity(entity)
    return entity


def _csv(values) -> str:
    return ",".join(sorted({str(v) for v in values if v not in (None, "")}))


def input_revision_hash(bars: Sequence[dict]) -> str:
    stable = [
        [int(b["BarStart"]), int(b.get("Revision", 1)), bar_hash_from_entity(b)]
        for b in sorted(bars, key=lambda x: int(x["BarStart"]))
    ]
    return hashlib.sha256(json.dumps(stable, separators=(",", ":")).encode()).hexdigest()


def _canonical_common(*, root: str, symbol: str, t: int, updated_at: int, input_bars: Sequence[dict], profile: dict) -> dict:
    dataset_ids = _csv(b.get("DatasetId") for b in input_bars) or profile["dataset_id"]
    dataset_versions = _csv(b.get("DatasetVersion") for b in input_bars) or profile["dataset_version"]
    origins = _csv(b.get("DataOrigin") for b in input_bars) or profile["data_origin"]
    revisions = [int(b.get("Revision", 1)) for b in input_bars]
    return {
        "PartitionKey": canonical_partition(root, t),
        "RowKey": row_key(t),
        "Root": root,
        "Timeframe": "5m",
        "BarStart": t,
        "BarClose": t + 300_000,
        "SourceSymbol": symbol,
        "Environment": profile["environment"],
        "DataClass": profile["data_class"],
        "IngestMode": MODE_DERIVED,
        "DatasetId": dataset_ids if "," not in dataset_ids else "MIXED_REAL" if profile["data_class"] == DATA_CLASS_REAL else "MIXED_TEST",
        "DatasetVersion": dataset_versions if "," not in dataset_versions else "MIXED",
        "RunId": profile["run_id"],
        "SchemaVersion": CANONICAL_SCHEMA_VERSION,
        "ProducerVersion": PRODUCER_VERSION,
        "LogicVersion": CANONICAL_LOGIC_VERSION,
        "InputSchemaVersion": RAW_SCHEMA_VERSION,
        "InputOrigins": origins,
        "InputDatasetIds": dataset_ids,
        "InputDatasetVersions": dataset_versions,
        "InputRevisionHash": input_revision_hash(input_bars),
        "InputRevisionMax": max(revisions) if revisions else 1,
        "Revision": 1,
        "RebuildCount": 0,
        "FirstBuiltAt": int(updated_at),
        "LastBuiltAt": int(updated_at),
        "UpdatedAt": int(updated_at),
    }


def canonical_from_1m(*, bars: Sequence[dict], symbol: str, root: str, bucket_start: int, updated_at: int, profile: dict) -> dict:
    if len(bars) != 5:
        raise ValueError("exactly 5 one-minute bars required")
    bars = sorted(bars, key=lambda b: int(b["BarStart"]))
    expected = [bucket_start + i * 60_000 for i in range(5)]
    actual = [int(b["BarStart"]) for b in bars]
    if actual != expected:
        raise ValueError("one-minute bars must be exact consecutive bucket members")
    environments = {b.get("Environment") for b in bars}
    classes = {b.get("DataClass") for b in bars}
    if environments != {profile["environment"]} or classes != {profile["data_class"]}:
        raise ValueError("input data environment/class mismatch")
    entity = _canonical_common(
        root=root,
        symbol=symbol,
        t=bucket_start,
        updated_at=updated_at,
        input_bars=bars,
        profile=profile,
    )
    entity.update({
        "Open": float(bars[0]["Open"]),
        "High": max(float(x["High"]) for x in bars),
        "Low": min(float(x["Low"]) for x in bars),
        "Close": float(bars[-1]["Close"]),
        "Volume": sum(int(x["Volume"]) for x in bars),
        "SourceType": "aggregated_from_1m",
        "SourceTimeframe": "1m",
        "Quality": "FULL_1M",
        "SourceBarCount": 5,
        "DataOrigin": ORIGIN_DERIVED_1M,
    })
    return entity


def canonical_from_native_5m(*, raw_bar: dict, symbol: str, root: str, updated_at: int, profile: dict) -> dict:
    t = int(raw_bar["BarStart"])
    entity = _canonical_common(
        root=root,
        symbol=symbol,
        t=t,
        updated_at=updated_at,
        input_bars=[raw_bar],
        profile=profile,
    )
    entity.update({
        "Open": float(raw_bar["Open"]),
        "High": float(raw_bar["High"]),
        "Low": float(raw_bar["Low"]),
        "Close": float(raw_bar["Close"]),
        "Volume": int(raw_bar["Volume"]),
        "SourceType": "native_5m",
        "SourceTimeframe": "5m",
        "Quality": "NATIVE_5M",
        "SourceBarCount": 1,
        "DataOrigin": ORIGIN_NATIVE_5M,
    })
    return entity


def status_entity(*, profile: dict, payload: dict, request_id: str, received_at: int, result: dict) -> dict:
    symbol = str(payload["symbol"])
    timeframe = tf_label(str(payload["timeframe"]))
    origin = profile["data_origin"]
    key = safe_key(f"{origin}_{symbol}_{timeframe}")
    latest = max(int(b["t"]) for b in payload["bars"])
    return {
        "PartitionKey": profile["environment"],
        "RowKey": key,
        "Environment": profile["environment"],
        "DataClass": profile["data_class"],
        "DataOrigin": origin,
        "IngestMode": profile["ingest_mode"],
        "DatasetId": profile["dataset_id"],
        "DatasetVersion": profile["dataset_version"],
        "RunId": profile["run_id"],
        "SchemaVersion": "ingeststatus_v1",
        "ProducerVersion": PRODUCER_VERSION,
        "Symbol": symbol,
        "Root": payload["root"],
        "Timeframe": timeframe,
        "LatestBarStart": latest,
        "LastRequestId": request_id,
        "LastReceivedAt": int(received_at),
        "Inserted": int(result.get("inserted", 0)),
        "Duplicates": int(result.get("duplicates", 0)),
        "Corrected": int(result.get("corrected", 0)),
        "Conflicts": int(result.get("conflicts", 0)),
        "CanonicalWritten": int(result.get("canonical_written", 0)),
        "PayloadBars": int(result.get("payload_bars", 0)),
    }


def process_payload(*, payload: dict, profile: dict, repo: Repository, request_id: str, received_at: int, updated_at: int) -> dict:
    error, details = validate_payload(payload)
    if error:
        raise ValueError(f"payload validation failed: {error} {details}")
    error, details = validate_profile(profile)
    if error:
        raise ValueError(f"profile validation failed: {error} {details}")

    symbol = str(payload["symbol"])
    root = str(payload["root"])
    timeframe = str(payload["timeframe"])
    bars = payload["bars"]

    counts = {"inserted": 0, "duplicates": 0, "corrected": 0, "conflicts": 0, "canonical_written": 0}
    affected = set()

    for bar in bars:
        incoming = raw_entity(
            payload=payload,
            bar=bar,
            profile=profile,
            request_id=request_id,
            received_at=received_at,
        )
        outcome = repo.upsert_raw(
            incoming,
            correction_mode=profile["correction_mode"],
            request_id=request_id,
            received_at=received_at,
        )
        action = outcome["action"]
        if action not in counts:
            raise ValueError(f"unknown raw write action: {action}")
        counts[action] += 1
        effective = outcome["entity"]

        if timeframe == "1":
            affected.add(bucket_start_5m(int(bar["t"])))
        else:
            entity = canonical_from_native_5m(
                raw_bar=effective,
                symbol=symbol,
                root=root,
                updated_at=updated_at,
                profile=profile,
            )
            c = repo.put_canonical(entity)
            if c["written"]:
                counts["canonical_written"] += 1

    if timeframe == "1":
        for bucket in sorted(affected):
            exact = []
            for i in range(5):
                item = repo.get_raw_bar(symbol=symbol, timeframe="1", t=bucket + i * 60_000)
                if item is None:
                    exact = []
                    break
                exact.append(item)
            if exact:
                entity = canonical_from_1m(
                    bars=exact,
                    symbol=symbol,
                    root=root,
                    bucket_start=bucket,
                    updated_at=updated_at,
                    profile=profile,
                )
                c = repo.put_canonical(entity)
                if c["written"]:
                    counts["canonical_written"] += 1

    counts["payload_bars"] = len(bars)
    repo.upsert_status(status_entity(
        profile=profile,
        payload=payload,
        request_id=request_id,
        received_at=received_at,
        result=counts,
    ))
    return counts
