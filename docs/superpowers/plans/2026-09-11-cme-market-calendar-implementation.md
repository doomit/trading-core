# CME Market Calendar Session Gate Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic CME equity-index session calendar, fail-closed market-state evaluator, Simple Paper new-risk gate, and Azure Blob mirror for MES/MNQ.

**Architecture:** `trading-core` owns a versioned calendar snapshot, schema/hash validation, pure session-state evaluation, feed/warm-up state, and exposure admission. `trading-live` packages the same canonical bytes, mirrors them to `tradinglivestore`, and verifies the deployed digest. Runtime never calls CME directly.

**Tech Stack:** Python 3.12, `zoneinfo`, `jsonschema`, pytest, GitHub Actions, Azure CLI/Blob Storage.

**Spec:** `docs/superpowers/specs/2026-09-11-cme-market-calendar-design.md`

## Global Constraints

- GitHub canonical + Azure Blob mirror + packaged local fallback.
- Trading path must not access CME HTTP.
- Calendar uncertainty, stale feed, warm-up, close-only, or closed state must never permit OPEN/ADD.
- CME weekly rules use `America/Chicago`; Simple Paper close-only remains 15:55 `America/New_York`.
- Reopen requires three consecutive advancing fresh 1-minute bars before new risk.
- 2028 candidate holiday guard windows are `UNVERIFIED` and fail closed for new risk.
- PAPER-only rollout.

---

### Task 1: Calendar artifact, schema, and loader

**Files:**
- Create: `config/market-calendar/cme-equity-index-v1.json`
- Create: `src/trading_core/schemas/cme_market_calendar_v1.schema.json`
- Create: `src/trading_core/cme_market_calendar.py`
- Create: `tests/test_cme_market_calendar.py`
- Modify: `pyproject.toml`

**Interfaces:**
- Produces: `load_calendar_bytes(payload: bytes) -> dict`, `canonical_calendar_digest(calendar: dict) -> str`, `exchange_session_state(symbol: str, now: datetime, calendar: dict) -> str`.

- [ ] **Step 1: Write failing loader/evaluator tests**

```python
from datetime import datetime, timezone
from trading_core.cme_market_calendar import load_calendar_bytes, exchange_session_state


def test_regular_weekend_is_closed(valid_calendar_bytes):
    cal = load_calendar_bytes(valid_calendar_bytes)
    assert exchange_session_state("MNQ", datetime(2026, 9, 12, 18, tzinfo=timezone.utc), cal) == "CLOSED"


def test_sunday_reopen_is_open(valid_calendar_bytes):
    cal = load_calendar_bytes(valid_calendar_bytes)
    assert exchange_session_state("MES", datetime(2026, 9, 13, 22, 0, tzinfo=timezone.utc), cal) == "OPEN"


def test_hash_mismatch_is_rejected(valid_calendar_dict):
    valid_calendar_dict["content_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="sha256"):
        load_calendar_bytes(json.dumps(valid_calendar_dict).encode())
```

- [ ] **Step 2: Run CI and verify RED**

Open/update the feature PR so `Core CI` runs. Expected: import/file failures for `trading_core.cme_market_calendar` and calendar artifact.

- [ ] **Step 3: Implement minimal schema/loader/evaluator**

```python
def exchange_session_state(symbol: str, now: datetime, calendar: dict) -> str:
    # priority: unverified guards -> verified overrides -> regular week
    ...
```

Canonical digest is SHA-256 over sorted compact JSON with `content_sha256` omitted.

- [ ] **Step 4: Run CI and verify GREEN**

Expected: Task 1 tests plus existing suite pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add CME equity-index calendar snapshot`.

---

### Task 2: Effective market state and three-bar warm-up

**Files:**
- Create: `src/trading_core/market_session_gate.py`
- Create: `tests/test_market_session_gate.py`
- Reuse: `src/trading_core/simple_paper_feed_safety.py`

**Interfaces:**
- Produces: `effective_market_state(...) -> str`, `can_increase_exposure(state: str) -> bool`, `update_warmup(previous: dict | None, latest_bar_end: str | None, exchange_state: str) -> dict`.

- [ ] **Step 1: Write failing state-priority tests**

```python
def test_calendar_closed_beats_fresh_bar():
    assert effective_market_state(exchange_state="CLOSED", close_only=False, feed_stale=False, warmup_count=3) == "MARKET_CLOSED"


def test_expected_open_stale_feed_is_data_stale():
    assert effective_market_state(exchange_state="OPEN", close_only=False, feed_stale=True, warmup_count=3) == "DATA_STALE"


def test_reopen_requires_three_advancing_bars():
    assert can_increase_exposure("WARMING_UP") is False
    assert can_increase_exposure("TRADING") is True
```

- [ ] **Step 2: Run CI and verify RED**

Expected: missing `market_session_gate` module/functions.

- [ ] **Step 3: Implement minimal pure state machine**

```python
def effective_market_state(*, exchange_state, close_only, feed_stale, warmup_count):
    if exchange_state == "UNVERIFIED": return "CALENDAR_UNVERIFIED"
    if exchange_state == "CLOSED": return "MARKET_CLOSED"
    if close_only: return "CLOSE_ONLY"
    if feed_stale: return "DATA_STALE"
    if warmup_count < 3: return "WARMING_UP"
    return "TRADING"
```

`update_warmup` increments only for strictly advancing one-minute bar timestamps; reset on CLOSED/UNVERIFIED or non-advancing input.

- [ ] **Step 4: Run CI and verify GREEN**

Expected: new state tests and full suite pass.

- [ ] **Step 5: Commit**

Commit message: `feat: add fail-closed market session gate`.

---

### Task 3: Enforce gate below Brain in Simple Paper execution

**Files:**
- Modify: `src/trading_core/simple_paper_execution_cycle.py`
- Modify: `src/trading_core/simple_paper_position_execution.py`
- Modify: `tests/test_simple_paper_execution_cycle.py`
- Create: `tests/test_simple_paper_session_gate.py`

**Interfaces:**
- `run_symbol_execution_cycle(..., market_state: str = "TRADING", ...)`
- `execute_flat_plan(..., allow_new_risk: bool = True, ...)`

- [ ] **Step 1: Write failing late-plan tests**

```python
def test_flat_position_cannot_reopen_after_close_only():
    result = execute_flat_plan(..., allow_new_risk=False)
    assert result["outcome"] == "NO_ACTION"
    assert result["position"]["status"] == "FLAT"


def test_open_position_can_still_exit_when_new_risk_is_blocked():
    # existing OPEN management remains active; only OPEN/ADD are blocked
    ...
```

Also test `ADD` cannot execute when `allow_new_risk=False`.

- [ ] **Step 2: Run CI and verify RED**

Expected: unexpected keyword / gate not enforced.

- [ ] **Step 3: Implement hard exposure gate**

Do not reject position management wholesale. Block only risk-increasing OPEN/ADD paths; keep STOP/TP/EXIT/REDUCE/EOD available.

- [ ] **Step 4: Run CI and verify GREEN**

Expected: full core suite passes.

- [ ] **Step 5: Commit**

Commit message: `fix: block Simple Paper new risk outside trading session`.

---

### Task 4: Azure mirror and packaged local fallback

**Files (`doomit/trading-live`):**
- Create: `function/config/market-calendar/cme-equity-index-v1.json`
- Create: `function/trading_market_calendar.py`
- Modify: `function/trading_simple_paper_execution_timer.py`
- Modify: `.github/workflows/simple-paper-minimal-stage.yml`
- Modify: `.github/workflows/simple-paper-active-cutover.yml`
- Create/modify matching tests under `tests/` if present.

**Interfaces:**
- `load_runtime_calendar()` prefers validated Azure Blob bytes when digest is valid and not older, otherwise packaged snapshot.
- Blob keys: `market-calendar/cme-equity-index-v1.json` and `market-calendar/cme-equity-index-v1.sha256`.

- [ ] **Step 1: Add failing packaging/digest checks**

Workflow/package checks must fail if canonical JSON is absent, digest mismatches, or package bytes differ from the core canonical artifact used by the source commit.

- [ ] **Step 2: Verify RED in branch workflow/PR checks**

Expected: missing packaged artifact or digest verification step.

- [ ] **Step 3: Add package copy, Blob upload, and startup fallback**

Workflow upload pattern:

```bash
sha256sum function/config/market-calendar/cme-equity-index-v1.json > /tmp/calendar.sha256
az storage blob upload --auth-mode login --account-name "$ACTIVE_STORAGE" --container-name runtime-config --name market-calendar/cme-equity-index-v1.json --file function/config/market-calendar/cme-equity-index-v1.json --overwrite true
```

Upload the detached digest and download/hash-verify after upload before declaring publication successful.

- [ ] **Step 4: Wire market state into Simple Paper timer**

The timer loads one validated in-memory calendar snapshot, evaluates exchange/calendar/feed/warm-up/close-only state, and passes only an `allow_new_risk`/state decision into core execution. No CME HTTP call occurs.

- [ ] **Step 5: Verify staged PAPER deployment**

Stage workflow must prove: expected calendar digest, Azure/local source status, no OPEN/ADD while blocked, and existing close/protection paths still function.

- [ ] **Step 6: Commit**

Commit message: `feat: mirror CME calendar into Simple Paper runtime`.

---

### Task 5: End-to-end verification and rollout evidence

**Files:**
- Modify: dashboard/status projection as needed in `trading-core` and `trading-live`.

- [ ] **Step 1: Expose state evidence**

Expose `calendar_sha256`, source, verified-through year, `exchange_session_state`, `effective_market_state`, warm-up count, and blocked reason.

- [ ] **Step 2: Run full core CI**

Expected: all tests pass with no warnings/errors.

- [ ] **Step 3: Run Simple Paper stage verification**

Expected: stage package contains canonical snapshot and Azure mirror hash equals packaged/GitHub hash.

- [ ] **Step 4: Keep rollout PAPER-only**

Do not alter any live trading adapter. Observe one maintenance reopen and one weekend reopen before promotion.
