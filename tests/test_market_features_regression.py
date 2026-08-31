import copy
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from trading_core.market_features import *

CT = ZoneInfo("America/Chicago")


def ms_local(y, m, d, h, minute=0):
    return int(datetime(y, m, d, h, minute, tzinfo=CT).astimezone(timezone.utc).timestamp() * 1000)


def one(t, o=100, h=101, l=99, c=100.5, v=10):
    return {"t": t, "tc": t + 60000, "o": o, "h": h, "l": l, "c": c, "v": v}


def five(bucket, base=100):
    return [one(bucket + i * 60000, base + i, base + i + 1, base + i - 1, base + i + 0.5, 10 + i) for i in range(5)]


def full_session(sd=date(2026, 8, 10), start_price=100.0):
    ts = expected_1m_timestamps(sd)
    out = []
    p = start_price
    for i, t in enumerate(ts):
        o = p
        c = o + (0.25 if i % 3 == 0 else -0.1 if i % 3 == 1 else 0.05)
        h = max(o, c) + 0.5
        l = min(o, c) - 0.5
        out.append(one(t, o, h, l, c, 100 + (i % 17)))
        p = c
    return out


@pytest.mark.parametrize("h,m,phase", [
    (17, 0, OVERNIGHT), (23, 59, OVERNIGHT), (0, 0, OVERNIGHT), (8, 29, OVERNIGHT),
    (8, 30, RTH), (14, 59, RTH), (15, 0, POST_RTH), (15, 59, POST_RTH),
    (16, 0, MAINTENANCE), (16, 59, MAINTENANCE),
])
def test_phase_boundaries(h, m, phase):
    assert session_phase_for_ms(ms_local(2026, 8, 10, h, m)) == phase


@pytest.mark.parametrize("h,expected", [
    (17, date(2026, 8, 11)), (23, date(2026, 8, 11)),
    (0, date(2026, 8, 10)), (8, date(2026, 8, 10)), (15, date(2026, 8, 10)),
])
def test_session_date_rollover(h, expected):
    assert session_date_for_ms(ms_local(2026, 8, 10, h)) == expected


@pytest.mark.parametrize("sd", [date(2026, 3, 9), date(2026, 11, 2), date(2026, 8, 10)])
def test_session_always_1380_minutes(sd):
    xs = expected_1m_timestamps(sd)
    assert len(xs) == 1380
    assert all(xs[i] - xs[i - 1] == 60000 for i in range(1, len(xs)))


def test_aggregate_exact_five():
    b = ms_local(2026, 8, 10, 8, 30)
    bars, partial = aggregate_1m_to_5m(five(b))
    assert len(bars) == 1 and partial == []
    x = bars[0]
    assert x["o"] == 100 and x["h"] == 105 and x["l"] == 99 and x["c"] == 104.5
    assert x["v"] == 60


@pytest.mark.parametrize("missing", [0, 1, 2, 3, 4])
def test_aggregate_rejects_any_missing_minute(missing):
    b = ms_local(2026, 8, 10, 8, 30)
    rows = five(b)
    rows.pop(missing)
    bars, partial = aggregate_1m_to_5m(rows)
    assert bars == [] and len(partial) == 1


def test_microstructure_counts_and_path():
    b = ms_local(2026, 8, 10, 8, 30)
    rows = [
        one(b, 100, 102, 99, 101, 10),
        one(b + 60000, 101, 102, 99, 100, 20),
        one(b + 120000, 100, 101, 99, 100, 30),
        one(b + 180000, 100, 103, 99, 102, 40),
        one(b + 240000, 102, 104, 101, 103, 50),
    ]
    x = aggregate_1m_to_5m(rows)[0][0]["micro"]
    assert x["MicroUpBars"] == 3
    assert x["MicroDownBars"] == 1
    assert x["MicroDojiBars"] == 1
    assert x["MicroEndRun"] == 2
    assert abs(x["MicroVolumeLast2Pct"] - 0.6) < 1e-12


def test_ema_and_wilder_seeding():
    e = EMA(20)
    vals = list(range(1, 21))
    for x in vals[:-1]:
        assert e.step(x) is None
    seed = e.step(vals[-1])
    assert seed == sum(vals) / 20
    assert abs(e.step(21) - (2 / 21 * 21 + 19 / 21 * seed)) < 1e-12

    a = WilderATR(3)
    assert a.step(3) is None
    assert a.step(6) is None
    assert a.step(9) == 6
    assert a.step(12) == 8


def test_qa_complete_and_missing_sessions():
    rows = full_session()
    q = qa_symbol(rows)
    assert q["issues"] == []
    assert q["metrics"]["raw_1m_count"] == 1380
    assert q["metrics"]["aggregated_5m_count"] == 276
    assert q["metrics"]["complete_sessions"] == 1

    missing = list(rows)
    missing.pop(100)
    q2 = qa_symbol(missing)
    assert q2["issues"]
    assert q2["metrics"]["missing_1m_inside_sessions"] == 1
    assert q2["metrics"]["partial_5m_buckets"] == 1


def test_qa_duplicate_bad_ohlc_and_cross_symbol_alignment():
    rows = full_session()
    dup = list(rows) + [copy.deepcopy(rows[0])]
    assert qa_symbol(dup)["metrics"]["duplicate_timestamps"] == 1

    bad = copy.deepcopy(rows)
    bad[10]["h"] = bad[10]["l"] - 1
    assert qa_symbol(bad)["metrics"]["ohlcv_invalid"] == 1

    aligned = copy.deepcopy(rows)
    assert qa_cross_symbol({"MES": rows, "MNQ": aligned})["issues"] == []
    aligned.pop(50)
    assert qa_cross_symbol({"MES": rows, "MNQ": aligned})["issues"]


def test_feature_count_phase_rth_indices_and_or_causality():
    fs = build_features("MES", "MES1!", full_session())
    assert len(fs) == 276
    assert sum(x["SessionPhase"] == OVERNIGHT for x in fs) == 186
    assert sum(x["SessionPhase"] == RTH for x in fs) == 78
    assert sum(x["SessionPhase"] == POST_RTH for x in fs) == 12
    r = [x for x in fs if x["IsRth"]]
    assert [x["BarIndexRth"] for x in r] == list(range(78))
    assert r[0]["IsRthOpenBar"] is True and r[-1]["IsRthCloseBar"] is True
    assert r[0]["OR5High"] is not None
    assert r[0]["OR15High"] is None and r[2]["OR15High"] is not None
    assert r[4]["OR30High"] is None and r[5]["OR30High"] is not None


def test_overnight_and_vwap_causality():
    fs = build_features("MES", "MES1!", full_session())
    ov = [x for x in fs if x["SessionPhase"] == OVERNIGHT]
    r = [x for x in fs if x["IsRth"]]
    assert all(x["OvernightComplete"] is False for x in ov)
    assert all(x["OvernightComplete"] is True for x in r)
    assert all(x["VWAPRth"] is None for x in ov)
    assert r[0]["VWAPRth"] is not None


def test_prev_session_levels_known_only_after_first_session():
    first = full_session(date(2026, 8, 10), 100)
    assert all(x["PrevSessionHigh"] is None for x in build_features("MES", "MES1!", first))
    rows = first + full_session(date(2026, 8, 11), 200)
    fs = build_features("MES", "MES1!", rows)
    second = [x for x in fs if x["SessionDate"] == "2026-08-11"]
    assert second and all(x["PrevSessionHigh"] is not None for x in second)


def test_warmup_hash_and_future_data_do_not_leak():
    rows = full_session()
    fs1 = build_features("MES", "MES1!", rows, warmup_bars=100)
    assert all(fs1[i]["IndicatorWarmup"] for i in range(100))
    assert fs1[100]["IndicatorWarmup"] is False
    assert deterministic_hash({"A": 1, "B": 2.5, "BuiltAt": 111}) == deterministic_hash({"B": 2.5, "A": 1, "BuiltAt": 999})

    cutoff = fs1[200]["BarStart"]
    rows2 = copy.deepcopy(rows)
    for row in rows2:
        if row["t"] >= cutoff + 300000:
            row["o"] += 1000
            row["h"] += 1000
            row["l"] += 1000
            row["c"] += 1000
    fs2 = build_features("MES", "MES1!", rows2, warmup_bars=100)
    assert feature_no_lookahead_signature(fs1, cutoff) == feature_no_lookahead_signature(fs2, cutoff)


def test_future_session_does_not_change_prior_features():
    s1 = full_session(date(2026, 8, 10), 100)
    f1 = build_features("MES", "MES1!", s1 + full_session(date(2026, 8, 11), 200))
    f2 = build_features("MES", "MES1!", s1 + full_session(date(2026, 8, 11), 1200))
    cutoff = max(x["BarStart"] for x in f1 if x["SessionDate"] == "2026-08-10")
    assert feature_no_lookahead_signature(f1, cutoff) == feature_no_lookahead_signature(f2, cutoff)


def test_canonical_qa_exact_value_and_quality():
    rows = full_session()
    bars, _ = aggregate_1m_to_5m(rows)
    canonical = [{**{k: b[k] for k in ("t", "tc", "o", "h", "l", "c", "v")}, "quality": "FULL_1M"} for b in bars]
    assert qa_symbol(rows, canonical)["issues"] == []

    bad_value = copy.deepcopy(canonical)
    bad_value[10]["c"] += 1
    assert qa_symbol(rows, bad_value)["metrics"]["canonical_value_mismatch"] == 1

    bad_quality = copy.deepcopy(canonical)
    bad_quality[0]["quality"] = "NATIVE_5M"
    assert qa_symbol(rows, bad_quality)["metrics"]["canonical_bad_quality"] == 1
