import copy
import random
import pytest

from trading_core.ingestion import *

BASE_T = 1787644200000


def bar(t, o=100.0, h=101.0, l=99.0, c=100.5, v=10, tf="1"):
    d = 60_000 if tf == "1" else 300_000
    return {"t": t, "tc": t + d, "o": o, "h": h, "l": l, "c": c, "v": v}


def payload(symbol="MES1!", root="MES", tf="1", bars=None, window=None, source="tradingview"):
    if bars is None:
        bars = [bar(BASE_T, tf=tf)]
    if window is None:
        window = len(bars)
    return {
        "schema": "tv_bars_v2",
        "source": source,
        "symbol": symbol,
        "root": root,
        "exchange": "CME_MINI",
        "timeframe": tf,
        "window": window,
        "sent_at": BASE_T + 60000,
        "bars": bars,
    }


def upload_body(p=None, **kw):
    body = {
        "schema": UPLOAD_SCHEMA_VERSION,
        "dataset_id": "tv_export_202608",
        "dataset_version": "1",
        "run_id": "run_abc",
        "correction_mode": CORRECTION_RECONCILE,
        "correction_reason": "reupload",
        "payload": p or payload(source="csv_upload"),
    }
    body.update(kw)
    return body


def five(start=BASE_T, base=100, volume=10):
    return [bar(start + i * 60000, o=base + i, h=base + i + 1, l=base + i - 1, c=base + i + 0.5, v=volume + i) for i in range(5)]


class InMemoryRepo:
    def __init__(self):
        self.raw = {}
        self.canonical = {}
        self.revisions = []
        self.conflicts = []
        self.status = {}

    def upsert_raw(self, entity, *, correction_mode, request_id, received_at):
        key = (entity["PartitionKey"], entity["RowKey"])
        cur = self.raw.get(key)
        if cur is None:
            self.raw[key] = dict(entity)
            return {"action": "inserted", "entity": self.raw[key]}
        if bar_hash_from_entity(cur) == bar_hash_from_entity(entity):
            return {"action": "duplicates", "entity": cur}
        if correction_mode != CORRECTION_RECONCILE:
            self.conflicts.append((copy.deepcopy(cur), copy.deepcopy(entity), correction_mode))
            return {"action": "conflicts", "entity": cur}
        self.revisions.append(copy.deepcopy(cur))
        replacement = dict(entity)
        replacement["Revision"] = int(cur.get("Revision", 1)) + 1
        replacement["CorrectionCount"] = int(cur.get("CorrectionCount", 0)) + 1
        replacement["FirstIngestedAt"] = cur.get("FirstIngestedAt", cur.get("IngestedAt"))
        replacement["CorrectedAt"] = received_at
        self.raw[key] = replacement
        return {"action": "corrected", "entity": replacement}

    def get_raw_bar(self, *, symbol, timeframe, t):
        return self.raw.get((raw_partition(symbol, timeframe, t), row_key(t)))

    def put_canonical(self, entity):
        key = (entity["PartitionKey"], entity["RowKey"])
        cur = self.canonical.get(key)
        if cur is None:
            self.canonical[key] = dict(entity)
            return {"written": True, "action": "created", "entity": self.canonical[key]}
        current_rank = QUALITY_RANK[cur["Quality"]]
        desired_rank = QUALITY_RANK[entity["Quality"]]
        if current_rank > desired_rank:
            return {"written": False, "action": "lower_priority", "entity": cur}
        same = all(cur.get(x) == entity.get(x) for x in (
            "Open", "High", "Low", "Close", "Volume", "Quality", "InputRevisionHash", "LogicVersion"
        ))
        if current_rank == desired_rank and same:
            return {"written": False, "action": "unchanged", "entity": cur}
        replacement = dict(entity)
        replacement["Revision"] = int(cur.get("Revision", 1)) + 1
        replacement["RebuildCount"] = int(cur.get("RebuildCount", 0)) + 1
        self.canonical[key] = replacement
        return {
            "written": True,
            "action": "priority_upgrade" if desired_rank > current_rank else "rebuilt",
            "entity": replacement,
        }

    def upsert_status(self, entity):
        self.status[(entity["PartitionKey"], entity["RowKey"])] = dict(entity)

    def get_canonical(self, root, t):
        return self.canonical.get((canonical_partition(root, t), row_key(t)))


def test_version_and_key_contracts():
    assert SERVICE_VERSION == "3.2.0"
    assert RAW_SCHEMA_VERSION == "rawbars_v2"
    assert CANONICAL_SCHEMA_VERSION == "canonical5m_v2"
    assert INGEST_SCHEMA_VERSION == "market_ingest_v2"
    assert safe_series_id("MES1!") == "MES1"
    assert tf_label("1") == "1m" and tf_label("5") == "5m"
    assert row_key(1000) < row_key(2000)
    assert bucket_start_5m(BASE_T + 4 * 60000) == BASE_T


@pytest.mark.parametrize("symbol,root", [("MES1!", "MES"), ("MNQ1!", "MNQ")])
def test_valid_payloads(symbol, root):
    assert validate_payload(payload(symbol=symbol, root=root)) == (None, {})


@pytest.mark.parametrize("mutator,error", [
    (lambda p: p.update(symbol="ES1!"), "invalid_symbol"),
    (lambda p: p.update(root="MNQ"), "symbol_root_mismatch"),
    (lambda p: p.update(timeframe="15"), "invalid_timeframe"),
    (lambda p: p.update(window=2), "window_bar_count_mismatch"),
])
def test_payload_rejects_invalid_contract(mutator, error):
    p = payload()
    mutator(p)
    assert validate_payload(p)[0] == error


def test_payload_rejects_bad_ohlcv_and_nonascending_rows():
    p = payload(bars=[bar(BASE_T), bar(BASE_T)])
    assert validate_payload(p)[0] == "bars_not_strictly_ascending"
    bad = bar(BASE_T, o=100, h=98, l=97, c=99)
    assert validate_payload(payload(bars=[bad]))[0] == "invalid_ohlc_invariants"
    negative = bar(BASE_T, v=-1)
    assert validate_payload(payload(bars=[negative]))[0] == "negative_volume"


def test_upload_profiles_and_envelope_preserve_environment_classification():
    assert validate_upload_request(upload_body()) == (None, {})
    live = profile_for_live(received_at=BASE_T)
    upload = profile_for_upload(upload_body())
    test = profile_for_test_live(request_id="abc")
    assert live["environment"] == ENV_PROD and live["data_class"] == DATA_CLASS_REAL
    assert live["correction_mode"] == CORRECTION_IMMUTABLE
    assert upload["data_origin"] == ORIGIN_CSV_UPLOAD and upload["correction_mode"] == CORRECTION_RECONCILE
    assert test["environment"] == ENV_TEST and test["data_class"] == DATA_CLASS_TEST

    envelope = make_ingest_envelope(payload=payload(), profile=live, request_id="r", received_at=BASE_T, transport_ms=2)
    assert validate_ingest_envelope(envelope) == (None, {})
    invalid = dict(live)
    invalid["data_class"] = DATA_CLASS_TEST
    assert validate_profile(invalid)[0] == "environment_data_class_mismatch"


def test_raw_metadata_and_hash_are_deterministic():
    profile = profile_for_live(received_at=BASE_T)
    entity = raw_entity(payload=payload(), bar=bar(BASE_T), profile=profile, request_id="r", received_at=BASE_T + 1)
    assert entity["Environment"] == ENV_PROD and entity["DataClass"] == DATA_CLASS_REAL
    assert entity["DataOrigin"] == ORIGIN_TRADINGVIEW_WEBHOOK
    assert entity["Revision"] == 1 and entity["CorrectionCount"] == 0
    assert len(entity["BarHash"]) == 64
    base = bar_hash_from_values(t=1, tc=2, o=3, h=4, l=2, c=3.5, v=5)
    assert bar_hash_from_values(t=1, tc=2, o=3, h=4, l=2, c=3.5, v=6) != base


def test_exact_five_builds_full_1m_and_four_never_fabricates_canonical():
    profile = profile_for_live(received_at=BASE_T)
    repo = InMemoryRepo()
    result = process_payload(payload=payload(bars=five()), profile=profile, repo=repo, request_id="r", received_at=1, updated_at=2)
    assert result["inserted"] == 5 and result["canonical_written"] == 1
    canonical = repo.get_canonical("MES", BASE_T)
    assert canonical["Quality"] == "FULL_1M"
    assert canonical["Close"] == 104.5
    assert canonical["Volume"] == sum(10 + i for i in range(5))

    repo2 = InMemoryRepo()
    process_payload(payload=payload(bars=five()[:4]), profile=profile, repo=repo2, request_id="r", received_at=1, updated_at=2)
    assert repo2.get_canonical("MES", BASE_T) is None


def test_duplicate_is_idempotent_and_live_change_conflicts_without_overwrite():
    profile = profile_for_live(received_at=BASE_T)
    repo = InMemoryRepo()
    p = payload(bars=five())
    process_payload(payload=p, profile=profile, repo=repo, request_id="r1", received_at=1, updated_at=2)
    duplicate = process_payload(payload=p, profile=profile, repo=repo, request_id="r2", received_at=3, updated_at=4)
    assert duplicate["duplicates"] == 5 and duplicate["corrected"] == 0 and not repo.revisions

    changed = copy.deepcopy(p)
    changed["bars"][2]["c"] += 0.25
    changed["bars"][2]["h"] += 0.25
    conflict = process_payload(payload=changed, profile=profile, repo=repo, request_id="r3", received_at=5, updated_at=6)
    assert conflict["conflicts"] == 1 and conflict["corrected"] == 0
    current = repo.get_raw_bar(symbol="MES1!", timeframe="1", t=BASE_T + 2 * 60000)
    assert current["Close"] == 102.5


def test_reconcile_correction_archives_and_rebuilds_canonical():
    repo = InMemoryRepo()
    live = profile_for_live(received_at=BASE_T)
    process_payload(payload=payload(bars=five()), profile=live, repo=repo, request_id="r1", received_at=1, updated_at=2)
    old_hash = repo.get_canonical("MES", BASE_T)["InputRevisionHash"]

    upload = profile_for_upload(upload_body(dataset_version="2"))
    changed = five()
    changed[4]["c"] += 1
    changed[4]["h"] += 1
    changed[4]["v"] += 100
    result = process_payload(
        payload=payload(bars=changed, source="csv_upload"),
        profile=upload,
        repo=repo,
        request_id="r2",
        received_at=3,
        updated_at=4,
    )
    assert result["corrected"] == 1 and len(repo.revisions) == 1
    raw = repo.get_raw_bar(symbol="MES1!", timeframe="1", t=BASE_T + 4 * 60000)
    assert raw["Revision"] == 2 and raw["CorrectionCount"] == 1
    canonical = repo.get_canonical("MES", BASE_T)
    assert canonical["Revision"] == 2 and canonical["RebuildCount"] == 1
    assert canonical["InputRevisionHash"] != old_hash
    assert canonical["DatasetId"] == "MIXED_REAL"


def test_native_5m_upgrades_to_full_1m_and_never_downgrades():
    repo = InMemoryRepo()
    upload = profile_for_upload(upload_body())
    native = payload(tf="5", bars=[bar(BASE_T, o=100, h=110, l=90, c=105, v=999, tf="5")], source="csv_upload")
    process_payload(payload=native, profile=upload, repo=repo, request_id="r5", received_at=1, updated_at=2)
    assert repo.get_canonical("MES", BASE_T)["Quality"] == "NATIVE_5M"

    process_payload(payload=payload(bars=five(), source="csv_upload"), profile=upload, repo=repo, request_id="r1", received_at=3, updated_at=4)
    full = repo.get_canonical("MES", BASE_T)
    assert full["Quality"] == "FULL_1M" and full["Revision"] == 2

    worse = payload(tf="5", bars=[bar(BASE_T, o=999, h=1000, l=998, c=999, v=9999, tf="5")], source="csv_upload")
    process_payload(payload=worse, profile=upload, repo=repo, request_id="r6", received_at=5, updated_at=6)
    assert repo.get_canonical("MES", BASE_T)["Quality"] == "FULL_1M"


def test_cross_environment_inputs_fail_closed():
    bars = []
    for b in five():
        entity = raw_entity(payload=payload(bars=[b]), bar=b, profile=profile_for_live(received_at=1), request_id="r", received_at=1)
        bars.append(entity)
    bars[0]["Environment"] = ENV_TEST
    with pytest.raises(ValueError, match="environment/class"):
        canonical_from_1m(
            bars=bars,
            symbol="MES1!",
            root="MES",
            bucket_start=BASE_T,
            updated_at=2,
            profile=profile_for_live(received_at=1),
        )


def test_random_aggregation_invariants_and_hash_stability():
    rng = random.Random(42)
    for _ in range(100):
        raw = []
        for i in range(5):
            o = rng.uniform(100, 200)
            c = rng.uniform(100, 200)
            h = max(o, c) + rng.uniform(0, 10)
            l = min(o, c) - rng.uniform(0, 10)
            v = rng.randint(0, 100000)
            raw.append(bar(BASE_T + i * 60000, o=o, h=h, l=l, c=c, v=v))
        repo = InMemoryRepo()
        process_payload(
            payload=payload(bars=raw),
            profile=profile_for_live(received_at=BASE_T),
            repo=repo,
            request_id="r",
            received_at=1,
            updated_at=2,
        )
        canonical = repo.get_canonical("MES", BASE_T)
        assert canonical["High"] >= max(canonical["Open"], canonical["Close"])
        assert canonical["Low"] <= min(canonical["Open"], canonical["Close"])
        assert canonical["Volume"] == sum(x["v"] for x in raw)
        assert len(canonical["InputRevisionHash"]) == 64
