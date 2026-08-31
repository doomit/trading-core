from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from hashlib import sha256
from typing import Any
from zoneinfo import ZoneInfo
import json
import math


CT = ZoneInfo("America/Chicago")
PT = ZoneInfo("America/Los_Angeles")
FEATURE_VERSION = "market_features_v1"
BUILD_VERSION = "1.0.0"

OVERNIGHT = "OVERNIGHT"
RTH = "RTH"
POST_RTH = "POST_RTH"
MAINTENANCE = "MAINTENANCE"


class DataQualityError(RuntimeError):
    pass


def as_float(v):
    if v is None:
        return None
    return float(v)


def session_date_for_ms(ms: int) -> date:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CT)
    d = dt.date()
    if dt.hour >= 17:
        d += timedelta(days=1)
    return d


def session_bounds_utc(session_date: date):
    start_local = datetime.combine(
        session_date - timedelta(days=1), time(17, 0), tzinfo=CT
    )
    end_local = datetime.combine(
        session_date, time(16, 0), tzinfo=CT
    )
    return start_local.astimezone(timezone.utc), end_local.astimezone(timezone.utc)


def session_phase_for_ms(ms: int) -> str:
    dt = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).astimezone(CT)
    minute = dt.hour * 60 + dt.minute
    if minute >= 17 * 60 or minute < 8 * 60 + 30:
        return OVERNIGHT
    if minute < 15 * 60:
        return RTH
    if minute < 16 * 60:
        return POST_RTH
    return MAINTENANCE


def expected_1m_timestamps(session_date: date) -> list[int]:
    start, end = session_bounds_utc(session_date)
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    return list(range(start_ms, end_ms, 60_000))


def safe_div(a, b):
    if b is None or abs(float(b)) < 1e-15:
        return None
    return float(a) / float(b)


def signed_direction(o, c):
    return 1 if c > o else -1 if c < o else 0


class EMA:
    """
    TradingView-style EMA:
    - no output before `length` observations
    - seed at observation `length` with SMA(length)
    - recursive alpha=2/(length+1) afterwards
    """

    def __init__(self, length=20):
        self.length = int(length)
        self.seed = []
        self.value = None

    def step(self, value):
        value = float(value)
        if self.value is None:
            self.seed.append(value)
            if len(self.seed) < self.length:
                return None
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
                return self.value
        alpha = 2.0 / (self.length + 1.0)
        self.value = alpha * value + (1.0 - alpha) * self.value
        return self.value


class WilderATR:
    def __init__(self, length=14):
        self.length = int(length)
        self.seed = []
        self.value = None

    def step(self, tr):
        tr = float(tr)
        if self.value is None:
            self.seed.append(tr)
            if len(self.seed) < self.length:
                return None
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
                return self.value
        self.value = ((self.length - 1) * self.value + tr) / self.length
        return self.value


def true_range(h, l, prev_close):
    if prev_close is None:
        return float(h) - float(l)
    return max(
        float(h) - float(l),
        abs(float(h) - float(prev_close)),
        abs(float(l) - float(prev_close)),
    )


def aggregate_1m_to_5m(rows: list[dict[str, Any]]):
    rows = sorted(rows, key=lambda r: int(r["t"]))
    grouped = defaultdict(list)
    for r in rows:
        t = int(r["t"])
        grouped[(t // 300_000) * 300_000].append(r)

    bars = []
    partial = []
    for bucket in sorted(grouped):
        rs = sorted(grouped[bucket], key=lambda r: int(r["t"]))
        expected = [bucket + i * 60_000 for i in range(5)]
        actual = [int(x["t"]) for x in rs]
        if actual != expected:
            partial.append({"bucket": bucket, "actual": actual, "expected": expected})
            continue

        o = float(rs[0]["o"])
        h = max(float(x["h"]) for x in rs)
        l = min(float(x["l"]) for x in rs)
        c = float(rs[-1]["c"])
        v = sum(int(x["v"]) for x in rs)

        dirs = [signed_direction(float(x["o"]), float(x["c"])) for x in rs]
        path_points = [o] + [float(x["c"]) for x in rs]
        path = sum(abs(path_points[i] - path_points[i - 1]) for i in range(1, len(path_points)))
        rng = h - l
        last2v = int(rs[-1]["v"]) + int(rs[-2]["v"])

        end_dir = dirs[-1]
        end_run = 0
        if end_dir:
            for d in reversed(dirs):
                if d == end_dir:
                    end_run += 1
                else:
                    break

        micro = {
            "MicroUpBars": sum(d == 1 for d in dirs),
            "MicroDownBars": sum(d == -1 for d in dirs),
            "MicroDojiBars": sum(d == 0 for d in dirs),
            "MicroTotalAbsBody": sum(abs(float(x["c"]) - float(x["o"])) for x in rs),
            "MicroMax1mRange": max(float(x["h"]) - float(x["l"]) for x in rs),
            "MicroNetMove": c - o,
            "MicroPath": path,
            "MicroPathEfficiency": safe_div(abs(c - o), path),
            "MicroLast1mBody": float(rs[-1]["c"]) - float(rs[-1]["o"]),
            "MicroLast1mDirection": end_dir,
            "MicroEndRun": end_run,
            "MicroVolumeLast2Pct": safe_div(last2v, v),
            "MicroCloseLocation": safe_div(c - l, rng),
        }

        pv = sum(
            ((float(x["h"]) + float(x["l"]) + float(x["c"])) / 3.0) * int(x["v"])
            for x in rs
        )
        bars.append(
            {
                "t": bucket,
                "tc": bucket + 300_000,
                "o": o,
                "h": h,
                "l": l,
                "c": c,
                "v": v,
                "micro": micro,
                "OneMinPV": pv,
                "OneMinVolume": v,
                "one_minute": rs,
            }
        )
    return bars, partial


def canonical_matches(agg, can, tol=1e-9):
    return (
        abs(float(agg["o"]) - float(can["o"])) <= tol
        and abs(float(agg["h"]) - float(can["h"])) <= tol
        and abs(float(agg["l"]) - float(can["l"])) <= tol
        and abs(float(agg["c"]) - float(can["c"])) <= tol
        and int(agg["v"]) == int(can["v"])
    )


def qa_symbol(rows: list[dict[str, Any]], canonical=None, require_complete=True):
    issues = []
    warnings = []
    metrics = {}

    rows = sorted(rows, key=lambda r: int(r["t"]))
    metrics["raw_1m_count"] = len(rows)
    if not rows:
        issues.append("empty input")
        return {"issues": issues, "warnings": warnings, "metrics": metrics}

    times = [int(r["t"]) for r in rows]
    duplicate_count = len(times) - len(set(times))
    metrics["duplicate_timestamps"] = duplicate_count
    if duplicate_count:
        issues.append(f"{duplicate_count} duplicate timestamps")

    ohlc_bad = 0
    maint_bars = 0
    for r in rows:
        o, h, l, c, v = map(float, (r["o"], r["h"], r["l"], r["c"], r["v"]))
        if not all(math.isfinite(x) for x in (o, h, l, c, v)):
            ohlc_bad += 1
        elif h < max(o, c) or l > min(o, c) or l > h or v < 0:
            ohlc_bad += 1
        if session_phase_for_ms(int(r["t"])) == MAINTENANCE:
            maint_bars += 1
    metrics["ohlcv_invalid"] = ohlc_bad
    metrics["maintenance_bars"] = maint_bars
    if ohlc_bad:
        issues.append(f"{ohlc_bad} invalid OHLCV rows")
    if maint_bars:
        issues.append(f"{maint_bars} bars inside CME maintenance hour")

    by_session = defaultdict(list)
    for r in rows:
        by_session[session_date_for_ms(int(r["t"]))].append(r)

    incomplete = []
    missing_in_sessions = 0
    phase_mismatch = 0
    complete_sessions = 0
    for sd, rs in sorted(by_session.items()):
        actual = {int(r["t"]) for r in rs}
        expected = set(expected_1m_timestamps(sd))
        missing = expected - actual
        extra = actual - expected
        if missing or extra:
            incomplete.append(str(sd))
            missing_in_sessions += len(missing)
        else:
            complete_sessions += 1

        phases = defaultdict(int)
        for r in rs:
            phases[session_phase_for_ms(int(r["t"]))] += 1
        if not missing and not extra:
            if phases[OVERNIGHT] != 930 or phases[RTH] != 390 or phases[POST_RTH] != 60:
                phase_mismatch += 1

    metrics["sessions"] = len(by_session)
    metrics["complete_sessions"] = complete_sessions
    metrics["incomplete_sessions"] = incomplete
    metrics["missing_1m_inside_sessions"] = missing_in_sessions
    metrics["session_phase_count_mismatches"] = phase_mismatch
    if require_complete and incomplete:
        issues.append(f"incomplete sessions: {','.join(incomplete)}")
    elif incomplete:
        warnings.append(f"incomplete sessions: {','.join(incomplete)}")
    if phase_mismatch:
        issues.append(f"{phase_mismatch} complete sessions have wrong phase counts")

    bars5, partial = aggregate_1m_to_5m(rows)
    metrics["aggregated_5m_count"] = len(bars5)
    metrics["partial_5m_buckets"] = len(partial)
    if partial:
        issues.append(f"{len(partial)} partial/non-exact 5m buckets")

    max_gap = 0.0
    max_1m_range = 0.0
    prev = None
    for r in rows:
        max_1m_range = max(max_1m_range, float(r["h"]) - float(r["l"]))
        if prev is not None:
            max_gap = max(max_gap, abs(float(r["o"]) - float(prev["c"])))
        prev = r
    metrics["max_1m_range"] = max_1m_range
    metrics["max_open_prev_close_gap"] = max_gap

    if canonical is not None:
        canmap = {int(x["t"]): x for x in canonical}
        aggmap = {int(x["t"]): x for x in bars5}
        missing_can = sorted(set(aggmap) - set(canmap))
        extra_can = sorted(set(canmap) - set(aggmap))
        mismatch = 0
        bad_quality = 0
        for t in set(aggmap) & set(canmap):
            if not canonical_matches(aggmap[t], canmap[t]):
                mismatch += 1
            if canmap[t].get("quality") not in (None, "FULL_1M"):
                bad_quality += 1
        metrics["canonical_missing"] = len(missing_can)
        metrics["canonical_extra"] = len(extra_can)
        metrics["canonical_value_mismatch"] = mismatch
        metrics["canonical_bad_quality"] = bad_quality
        if missing_can or extra_can or mismatch or bad_quality:
            issues.append(
                "canonical mismatch "
                f"missing={len(missing_can)} extra={len(extra_can)} "
                f"values={mismatch} quality={bad_quality}"
            )

    return {"issues": issues, "warnings": warnings, "metrics": metrics}


def qa_cross_symbol(rows_by_root):
    roots = sorted(rows_by_root)
    if len(roots) < 2:
        return {"issues": [], "metrics": {}}
    base = roots[0]
    base_ts = {int(r["t"]) for r in rows_by_root[base]}
    issues = []
    metrics = {}
    for root in roots[1:]:
        ts = {int(r["t"]) for r in rows_by_root[root]}
        only_base = len(base_ts - ts)
        only_other = len(ts - base_ts)
        metrics[f"{base}_only_vs_{root}"] = only_base
        metrics[f"{root}_only_vs_{base}"] = only_other
        if only_base or only_other:
            issues.append(
                f"timestamp mismatch {base}/{root}: "
                f"{base}_only={only_base}, {root}_only={only_other}"
            )
    return {"issues": issues, "metrics": metrics}


def precompute_session_levels(bars5):
    grouped = defaultdict(list)
    for b in bars5:
        grouped[session_date_for_ms(int(b["t"]))].append(b)
    levels = {}
    for sd, bars in grouped.items():
        bars = sorted(bars, key=lambda x: int(x["t"]))
        rth = [b for b in bars if session_phase_for_ms(int(b["t"])) == RTH]
        levels[sd] = {
            "SessionHigh": max(float(b["h"]) for b in bars),
            "SessionLow": min(float(b["l"]) for b in bars),
            "SessionClose": float(bars[-1]["c"]),
            "RthHigh": max(float(b["h"]) for b in rth) if rth else None,
            "RthLow": min(float(b["l"]) for b in rth) if rth else None,
            "RthClose": float(rth[-1]["c"]) if rth else None,
        }
    return levels


def deterministic_hash(entity: dict[str, Any]) -> str:
    ignored = {"BuiltAt"}
    stable = {k: entity[k] for k in sorted(entity) if k not in ignored}
    payload = json.dumps(stable, sort_keys=True, separators=(",", ":"), default=str)
    return sha256(payload.encode()).hexdigest()


def build_features(root: str, symbol: str, rows1m: list[dict[str, Any]], warmup_bars=100):
    bars5, partial = aggregate_1m_to_5m(rows1m)
    if partial:
        raise DataQualityError(f"cannot build features with {len(partial)} partial 5m buckets")

    levels = precompute_session_levels(bars5)
    sessions = sorted(levels)
    prev_session = {sessions[i]: (sessions[i - 1] if i else None) for i in range(len(sessions))}

    ema = EMA(20)
    atr = WilderATR(14)
    prev_close = None
    vol_window = []

    current_sd = None
    session_index = -1
    rth_index = -1
    session_hi = session_lo = None
    rth_hi = rth_lo = None
    overnight_hi = overnight_lo = None
    session_pv = session_vol = 0
    rth_pv = rth_vol = 0
    rth_open = None
    or_bars = []

    out = []

    for global_i, b in enumerate(bars5):
        t = int(b["t"])
        sd = session_date_for_ms(t)
        phase = session_phase_for_ms(t)

        if sd != current_sd:
            current_sd = sd
            session_index = -1
            rth_index = -1
            session_hi = session_lo = None
            rth_hi = rth_lo = None
            overnight_hi = overnight_lo = None
            session_pv = session_vol = 0
            rth_pv = rth_vol = 0
            rth_open = None
            or_bars = []

        session_index += 1
        if phase == RTH:
            rth_index += 1

        h, l, o, c, v = map(float, (b["h"], b["l"], b["o"], b["c"], b["v"]))

        ema20 = ema.step(c)
        tr = true_range(h, l, prev_close)
        atr14 = atr.step(tr)
        prev_close = c

        vol_window.append(v)
        if len(vol_window) > 20:
            vol_window.pop(0)
        volume_sma20 = sum(vol_window) / 20.0 if len(vol_window) == 20 else None
        rel_volume20 = safe_div(v, volume_sma20)

        session_hi = h if session_hi is None else max(session_hi, h)
        session_lo = l if session_lo is None else min(session_lo, l)

        session_pv += float(b["OneMinPV"])
        session_vol += int(b["OneMinVolume"])
        vwap_session = safe_div(session_pv, session_vol)

        if phase == OVERNIGHT:
            overnight_hi = h if overnight_hi is None else max(overnight_hi, h)
            overnight_lo = l if overnight_lo is None else min(overnight_lo, l)

        if phase == RTH:
            if rth_open is None:
                rth_open = o
            rth_hi = h if rth_hi is None else max(rth_hi, h)
            rth_lo = l if rth_lo is None else min(rth_lo, l)
            rth_pv += float(b["OneMinPV"])
            rth_vol += int(b["OneMinVolume"])
            or_bars.append(b)

        vwap_rth = safe_div(rth_pv, rth_vol) if rth_vol else None
        overnight_complete = phase in (RTH, POST_RTH)

        def or_level(n, side):
            needed = n // 5
            if len(or_bars) < needed:
                return None
            subset = or_bars[:needed]
            if side == "h":
                return max(float(x["h"]) for x in subset)
            return min(float(x["l"]) for x in subset)

        prev_sd = prev_session[sd]
        pl = levels.get(prev_sd) if prev_sd else None
        rth_open_gap = (
            rth_open - pl["RthClose"]
            if rth_open is not None and pl and pl["RthClose"] is not None
            else None
        )

        rng = h - l
        body = c - o
        upper = h - max(o, c)
        lower = min(o, c) - l
        bar_close_loc = safe_div(c - l, rng)

        f = {
            "FeatureVersion": FEATURE_VERSION,
            "BuildVersion": BUILD_VERSION,
            "Root": root,
            "Symbol": symbol,
            "BarStart": t,
            "BarClose": int(b["tc"]),
            "FeatureAsOf": int(b["tc"]),
            "SessionDate": sd.isoformat(),
            "SessionPhase": phase,
            "BarIndexSession": session_index,
            "BarIndexRth": rth_index if phase == RTH else -1,
            "IsSessionOpenBar": session_index == 0,
            "IsRth": phase == RTH,
            "IsRthOpenBar": phase == RTH and rth_index == 0,
            "IsRthCloseBar": phase == RTH and rth_index == 77,
            "Open": o,
            "High": h,
            "Low": l,
            "Close": c,
            "Volume": int(v),
            "Range": rng,
            "Body": body,
            "BodyPctOfRange": safe_div(abs(body), rng),
            "UpperTail": upper,
            "LowerTail": lower,
            "CloseLocation": bar_close_loc,
            "Direction": signed_direction(o, c),
            "EMA20": ema20,
            "ATR14": atr14,
            "IndicatorWarmup": global_i < warmup_bars,
            "VolumeSma20": volume_sma20,
            "RelVolume20": rel_volume20,
            "VWAPSession": vwap_session,
            "VWAPRth": vwap_rth,
            "SessionHighSoFar": session_hi,
            "SessionLowSoFar": session_lo,
            "RthHighSoFar": rth_hi,
            "RthLowSoFar": rth_lo,
            "OvernightHighKnown": overnight_hi,
            "OvernightLowKnown": overnight_lo,
            "OvernightComplete": overnight_complete,
            "PrevSessionHigh": pl["SessionHigh"] if pl else None,
            "PrevSessionLow": pl["SessionLow"] if pl else None,
            "PrevSessionClose": pl["SessionClose"] if pl else None,
            "PrevRthHigh": pl["RthHigh"] if pl else None,
            "PrevRthLow": pl["RthLow"] if pl else None,
            "PrevRthClose": pl["RthClose"] if pl else None,
            "RthOpen": rth_open,
            "RthOpenGapFromPrevClose": rth_open_gap,
            "OR5High": or_level(5, "h"),
            "OR5Low": or_level(5, "l"),
            "OR15High": or_level(15, "h"),
            "OR15Low": or_level(15, "l"),
            "OR30High": or_level(30, "h"),
            "OR30Low": or_level(30, "l"),
            "CloseVsEMA20ATR": safe_div(c - ema20, atr14) if ema20 is not None else None,
            "CloseVsVWAPRthATR": safe_div(c - vwap_rth, atr14) if vwap_rth is not None else None,
            "Input1mCount": 5,
            "InputQuality": "FULL_1M",
        }
        f.update(b["micro"])
        f["FeatureHash"] = deterministic_hash(f)
        out.append(f)

    return out


def feature_no_lookahead_signature(features, through_ms):
    keys = (
        "BarStart",
        "EMA20",
        "ATR14",
        "VWAPSession",
        "VWAPRth",
        "SessionHighSoFar",
        "SessionLowSoFar",
        "RthHighSoFar",
        "RthLowSoFar",
        "OvernightHighKnown",
        "OvernightLowKnown",
        "OR5High",
        "OR15High",
        "OR30High",
        "FeatureHash",
    )
    return [
        tuple(f.get(k) for k in keys)
        for f in features
        if int(f["BarStart"]) <= int(through_ms)
    ]
