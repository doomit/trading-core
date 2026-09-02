# A3 PAPER Strategy/Risk Implementation Plan

> Scope: PAPER-only. Follow strict RED -> GREEN TDD. Do not activate Azure runtime in place.

## Test discipline for every task

- Before production behavior changes, add the narrowest acceptance/regression test and observe it fail for the intended reason.
- After each production change, run the changed-component tests plus the P0 fast gate.
- At major A3 profile/release boundaries run full public `pytest -q` on the exact candidate SHA.
- Ordinary tests live/run in public `doomit/trading-core` or worker execution; private Actions are not used for ordinary validation.

## Task 1 — P0 feed trust/freshness public contract

**Files:**
- Modify `tests/test_runtime_config.py`
- Modify `tests/test_configurable_risk.py`
- Modify `src/trading_core/runtime_config.py`
- Modify `src/trading_core/configurable_risk.py`

### 1A RED — runtime-configurable feed age

Add tests proving:
1. A legacy A2 config with no `max_feed_age_seconds` still parses and defaults to 90 seconds.
2. Explicit `max_feed_age_seconds=180` parses and round-trips.
3. Non-positive and values above the compiled PAPER envelope are rejected.

Run the new runtime-config tests before implementation and record RED caused by the missing field/typed policy behavior.

### 1B RED — trusted feed older than 90s can be valid under A3 config

Add risk regression tests:
1. With trusted PROD/REAL/tradingview market and `healthy=True`, feed age 120 seconds plus config max age 180 is not rejected as `UNTRUSTED_FEED` or `STALE_FEED`; the otherwise-valid plan reaches `RISK_APPROVED`.
2. Same config with feed age 181 seconds is rejected exactly as `STALE_FEED`.
3. Bad provenance remains `UNTRUSTED_FEED` even when fresh.

Run the new risk tests and record RED: current code uses static 90 seconds and should reject the 120-second case as `STALE_FEED`.

### 1C GREEN — runtime config field

Implement the smallest config change:
- Add `RiskConfig.max_feed_age_seconds: int`.
- Parse optional `risk.max_feed_age_seconds`; default missing value to 90 for immutable A2 compatibility.
- Validate as positive integer with a compiled PAPER upper bound of 900 seconds.
- Serialize the field so new versioned configs are explicit.

Run:
- exact new runtime-config tests;
- full `tests/test_runtime_config.py`;
- P0 fast gate.

### 1D GREEN — configurable risk uses config age

Replace the config-aware gateway's static `MAX_FEED_AGE_SECONDS` use with the validated runtime-config value. Keep provenance/structural `market.healthy` under `UNTRUSTED_FEED`; feed age independently yields `STALE_FEED`.

Run:
- exact new risk regressions;
- full `tests/test_configurable_risk.py`;
- P0 fast gate.

Do not change legacy non-configurable `RiskGateway` in this slice unless a failing acceptance test proves it is required.

## Task 2 — P0 private snapshot adapter trust/freshness separation

**Files (private adapter, worker-local tests):**
- Modify `doomit/trading-live/tests/test_trading_execution_snapshot_builder.py`
- Modify `doomit/trading-live/function/trading_execution_snapshot_builder.py`

First inspect current tests and data-quality assumptions.

RED test: a structurally valid 120-second-old TradingView snapshot must retain trusted/structurally healthy provenance; age is represented by `feed_as_of` and left for RiskGateway stale evaluation. The adapter must not encode the 90-second age threshold into `healthy`.

GREEN: remove age-derived `healthy` semantics while preserving any existing structural quality checks. Run only the private component tests locally/worker-side; do not start private Actions.

Activation is blocked on the versioned Azure rollout path.

## Task 3 — P0 Event Brain capacity effective end-to-end

**Public files:**
- `tests/test_brain_trigger_schema.py`
- `src/trading_core/schemas/brain_trigger_v1.schema.json` only if a contract gap is proven

**Private adapter files:**
- `doomit/trading-live/tests/test_trading_event_capacity.py`
- `doomit/trading-live/tests/test_trading_brain_trigger.py`
- corresponding function modules

Investigate actual missing-capacity path first. Do not rewrite already-working projection code.

RED tests should target the confirmed failing boundary, such as enrichment-failure observability or consumer contract mismatch. GREEN only that boundary. Verify capacity-positive event payload contains current capacity and failure is separately observable from strategy `NO_TRADE`.

Run P0 fast public gate after any public logic change and exact private component tests worker-side.

## Task 4 — P0 runtime/Deep Brain liveness

Respect current runtime-health/live-ingress leases. Use diagnostic evidence to locate the failing layer before code changes. Any liveness code fix gets a failing acceptance test first. No direct active Azure replacement.

Acceptance: open-session feed/context advances continuously, Scheduled Deep Brain heartbeat/coverage stays current, legitimate Event Brain anomalies can process, no stuck execution rows, PAPER gates healthy, live money disabled.

## Task 5 — P0 dependency versioned Azure rollout

Complete immutable candidate staging and explicit switch/rollback. Ordinary tests remain public/local. Azure-specific capability/stage/switch verification may use private Actions when necessary.

No P0 code from Tasks 1-4 is activated until this path is verified.

## Task 6 — P1 A3 strategy scenarios

**Public test-first artifacts:** add deterministic/scenario tests around strategy/Brain policy contracts before changing any strategy guidance or config behavior.

Required scenarios:
- strong directional body + EMA/VWAP alignment + structural breakout/acceptance can create a continuation candidate without mandatory pullback;
- decisive reclaim plus follow-through can create a candidate;
- extension alone does not force `NO_TRADE` when structural risk/reward remains valid;
- mixed mid-range chop still produces `NO_TRADE`;
- no same-bar execution/lookahead.

Preserve A2 unchanged. Add new profile `PA_AGGRESSIVE_A3_TREND` only after RED scenario coverage exists.

Run component tests and P0 fast gate after every behavior change.

## Task 7 — P1 A3 risk version

Test first, then create an immutable A3 runtime config with:
- min confidence 0.55
- target risk $750
- max risk $1500
- max open micros 20
- max daily realized loss $7500
- max consecutive losses 5
- max entries/session 40
- max feed age 180s

Acceptance tests must also prove mandatory stops, stale/session/kill/config identity and safe quantity downsize still work.

## Task 8 — P1 release validation

Create A2-vs-A3 deterministic replay/shadow evidence including candidate/no-trade/rejection reason counts and relevant PnL diagnostics. Do not claim proven edge from behavioral validation alone.

Run full public `pytest -q` on exact A3 RC SHA. Stage isolated Azure candidate, verify PAPER E2E, then explicit switch with rollback target retained. Confirm `live_money=NOT_CONNECTED_NOT_AUTHORIZED`.
