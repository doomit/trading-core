# CME Market Calendar Snapshot and Session Gate Design

**Date:** 2026-09-11

## Goal

Make MES/MNQ market-session handling deterministic and fail-closed without depending on live CME network access in the trading path. GitHub is the canonical source of a versioned CME calendar snapshot, Azure Blob Storage mirrors the exact same bytes and digest, and the deployed runtime carries the same snapshot as a local fallback. Runtime execution combines that calendar with live bar freshness and reopen warm-up before allowing any new position.

## Scope

This design covers:

- MES and MNQ CME Globex regular weekly trading hours.
- Published CME holiday and early-close overrides for 2026 and 2027.
- 2028 candidate holiday guard windows marked unverified and fail-closed until CME publishes/finalizes authoritative hours.
- Canonical snapshot storage in `trading-core`.
- Exact-byte mirror in Azure Blob Storage.
- Local packaged fallback in the deployed Azure Function package.
- Schema and SHA-256 validation.
- Runtime market/session state evaluation.
- Hard admission gating for OPEN/ADD after the strategy close-only boundary and whenever exchange/calendar/feed state is unsafe.
- Reopen warm-up requiring three consecutive fresh 1-minute bars before new entries are admitted.

This design does not change strategy logic, signal generation, position sizing, or profit/loss rules. It does not make CME HTTP availability part of runtime execution.

## External Authority

CME is the authority for exchange trading hours and holiday schedules.

As of 2026-09-11, CME explicitly publishes downloadable Globex holiday calendars for calendar years 2026 and 2027 and states that holiday trading hours are subject to change and are usually finalized approximately two weeks before each holiday. Therefore 2028 holiday trading-hour details cannot be treated as authoritative today.

For MES/MNQ regular hours, the baseline rule is the CME Globex Micro E-mini equity-index schedule: Sunday 17:00 CT through Friday 16:00 CT with the daily maintenance break from 16:00 CT to 17:00 CT. All schedule calculations use `America/Chicago`, not fixed UTC offsets, so DST is handled by the timezone database.

## Source-of-Truth Model

### GitHub canonical

The canonical artifact lives in `trading-core`:

- `config/market-calendar/cme-equity-index-v1.json`
- `src/trading_core/schemas/cme_market_calendar_v1.schema.json`

Only a committed, schema-valid snapshot is eligible for production deployment.

### Azure mirror

The Azure mirror stores the exact same canonical bytes in storage account `tradinglivestore` under a stable blob key such as:

- `market-calendar/cme-equity-index-v1.json`
- `market-calendar/cme-equity-index-v1.sha256`

The JSON payload itself also contains its canonical digest metadata. The detached `.sha256` object exists for operational inspection and deployment validation.

### Local fallback

The same canonical JSON file is copied into the deployed Function App package. Runtime startup may prefer the Azure mirror when it validates successfully, but must always be able to continue using the packaged local snapshot if Azure Blob read fails or returns invalid data.

The trading path never fetches CME directly.

## Snapshot Schema

The v1 snapshot has one purpose: answer whether the CME equity-index market is expected to be open at an instant and whether that answer is verified.

Representative shape:

```json
{
  "schema": "cme_market_calendar_v1",
  "calendar_id": "cme-equity-index",
  "venue": "CME_GLOBEX",
  "products": ["MES", "MNQ"],
  "timezone": "America/Chicago",
  "generated_at": "2026-09-11T00:00:00Z",
  "effective_from": "2026-01-01T00:00:00Z",
  "effective_until": "2028-12-31T23:59:59Z",
  "source": {
    "authority": "CME Group",
    "trading_hours_url": "https://www.cmegroup.com/trading-hours.html",
    "verified_through_year": 2027,
    "notes": "2028 holiday windows are guard-only until CME publishes authoritative schedules"
  },
  "regular_week": {
    "sunday_open": "17:00:00",
    "friday_close": "16:00:00",
    "daily_maintenance_start": "16:00:00",
    "daily_maintenance_end": "17:00:00"
  },
  "overrides": [],
  "guard_windows": [],
  "content_sha256": "..."
}
```

`content_sha256` is computed over a canonical serialization with the digest field omitted. Loader validation recomputes it before accepting the artifact.

## Override Semantics

Each authoritative 2026/2027 override is an explicit UTC or timezone-aware interval with one of:

- `CLOSED`
- `OPEN`
- `EARLY_CLOSE`
- `LATE_OPEN`

Each record includes:

- `start`
- `end`
- `status`
- `holiday_name`
- `verification = VERIFIED`
- `source_year`
- optional `source_note`

Runtime does not infer an early close merely from a holiday name. A verified interval must come from the published CME schedule.

## 2028 Guard Windows

Because CME has not published authoritative 2028 holiday trading hours today, 2028 dates are not encoded as normal tradeable overrides.

Instead, deterministic U.S. market-holiday candidates are generated into `guard_windows` for the holidays CME commonly observes for Globex equity-index products, including New Year, MLK Day, Presidents Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, and Christmas.

Each 2028 guard window is marked:

```text
verification = UNVERIFIED
policy = FAIL_CLOSED_FOR_NEW_RISK
```

Within an unverified guard window:

- new OPEN is prohibited;
- ADD is prohibited;
- existing positions may still be reduced or closed;
- protective stops remain authoritative;
- system health exposes `CALENDAR_UNVERIFIED`;
- a late/duplicate market bar does not override the calendar safety state.

When CME later publishes/finalizes the authoritative schedule, those guard windows are replaced with verified overrides in a new committed snapshot.

## Runtime State Model

Calendar state, feed state, and strategy state are separate concerns.

### Exchange calendar state

`exchange_session_state(symbol, now, calendar)` returns:

- `OPEN`
- `CLOSED`
- `UNVERIFIED`

Priority:

1. matching unverified guard window -> `UNVERIFIED`;
2. matching verified override -> apply override;
3. otherwise evaluate regular weekly session in `America/Chicago`.

### Feed state

The existing stale-feed safety remains independent of calendar state.

At minimum:

- `LIVE`: latest observed 1-minute bar is advancing and fresh;
- `STALE`: expected-open market has not produced a fresh bar within the configured freshness threshold;
- `WARMING_UP`: exchange has transitioned from non-open to open, but runtime has not yet observed three consecutive new 1-minute bars.

No-bar is never used by itself to conclude that the exchange is closed. Calendar decides expected exchange status; bars confirm data health.

### Effective trading state

`effective_market_state(...)` returns one of:

- `TRADING`
- `CLOSE_ONLY`
- `MARKET_CLOSED`
- `CALENDAR_UNVERIFIED`
- `DATA_STALE`
- `WARMING_UP`

Evaluation priority is fail-closed:

```text
calendar UNVERIFIED -> CALENDAR_UNVERIFIED
calendar CLOSED     -> MARKET_CLOSED
strategy close-only -> CLOSE_ONLY
feed stale          -> DATA_STALE
reopen not warmed   -> WARMING_UP
otherwise           -> TRADING
```

The strategy close-only policy remains separate from CME exchange hours. Current Simple Paper policy is 15:55 ET, which corresponds to 14:55 CT. Once that boundary is reached for the strategy day, no new OPEN or ADD is allowed even though CME Globex remains open later.

## Admission Rules

Every new-risk execution path must call one authoritative admission function before changing state:

```text
can_increase_exposure(symbol, now, market_state) -> bool
```

It returns true only for `TRADING`.

Therefore:

- `OPEN` requires `TRADING`.
- `ADD` requires `TRADING`.
- `UPDATE` that only tightens protection may be allowed outside `TRADING` if it cannot increase risk.
- `REDUCE`, `EXIT`, protective stop, take-profit, EOD forced close, and emergency flatten remain allowed when appropriate.

This gate must sit below Brain/plan generation so a valid-looking late plan cannot bypass session safety.

## Friday and Weekend Behavior

For a normal week in `America/Chicago`:

- Friday 16:00 CT: exchange becomes `MARKET_CLOSED`.
- Saturday: remains `MARKET_CLOSED`.
- Sunday before 17:00 CT: remains `MARKET_CLOSED`.
- Sunday 17:00 CT: exchange calendar becomes `OPEN`, but effective state begins as `WARMING_UP`.
- After three consecutive advancing, fresh 1-minute bars: effective state becomes `TRADING`, unless another higher-priority rule blocks entries.

The current strategy close-only boundary independently prevents fresh risk after 15:55 ET on each trading day.

## Daily Maintenance Behavior

Monday through Thursday at 16:00-17:00 CT the exchange state is `CLOSED` from the regular-week rule. At 17:00 CT reopen, the same three-bar warm-up applies before `TRADING` resumes.

This intentionally treats daily maintenance reopen and Sunday reopen the same way.

## Snapshot Loading and Precedence

At process startup or deployment initialization:

1. Load packaged local canonical snapshot and validate schema, supported calendar ID, effective range, and SHA-256.
2. Attempt to read Azure mirror.
3. If Azure mirror validates and its `generated_at` is not older than the packaged version, use Azure.
4. If Azure is unavailable, malformed, hash-invalid, unsupported, or older, use packaged local.
5. If neither source validates, runtime enters a calendar safety fault and prohibits new risk.

No source switch occurs mid-execution-cycle. A process may refresh between cycles or on a low-frequency control-plane interval, but a refresh must atomically replace the validated in-memory calendar.

## Publication Flow

Calendar publication is control-plane work, not trading-path work.

1. Fetch/inspect official CME schedule.
2. Generate candidate JSON.
3. Validate schema and semantic invariants.
4. Run deterministic session-boundary tests.
5. Commit canonical snapshot to `trading-core`.
6. CI computes/verifies the SHA-256.
7. Approved deployment copies the exact committed bytes into the Function package.
8. Deployment workflow uploads the same bytes and detached digest to `tradinglivestore`.
9. Deployment verifies Azure bytes hash to the GitHub canonical digest before considering publication complete.

A later calendar refresh creates a normal reviewed commit; it never edits Azure first.

## Repository Responsibilities

### `doomit/trading-core`

Owns:

- calendar schema;
- canonical snapshot;
- calendar loader and digest validation;
- exchange-session evaluator;
- effective market-state logic;
- exposure-admission gate;
- unit tests for all boundaries.

### `doomit/trading-live`

Owns:

- packaging the committed canonical snapshot into the Azure Function deployment;
- Azure Blob mirror upload/verification using the existing OIDC/deployment path;
- startup wiring from Blob/local snapshot into `trading-core` session APIs;
- deployment and smoke tests proving the active runtime sees the expected calendar digest.

### `doomit/trading-runtime`

No canonical calendar ownership. Runtime data such as bars remains runtime state, not calendar configuration.

## Observability

Dashboard/runtime status should expose at least:

- `calendar_id`
- active `calendar_sha256`
- calendar source: `AZURE_MIRROR` or `LOCAL_FALLBACK`
- `calendar_generated_at`
- `calendar_verified_through_year`
- current `exchange_session_state`
- current `feed_state`
- current `effective_market_state`
- warm-up consecutive-bar count
- next known session transition if available
- reason new exposure is blocked

A fallback to local snapshot is not itself a trading halt if the local snapshot validates. A hash mismatch or no valid snapshot is a hard new-risk block.

## Failure Policy

The design follows one rule: uncertainty may reduce capability but must never manufacture permission to take new risk.

Examples:

- CME website unreachable: no runtime impact.
- Azure Blob unreachable: use validated packaged snapshot.
- Azure hash mismatch: reject Azure and use packaged snapshot.
- packaged and Azure snapshots both invalid: block OPEN/ADD.
- expected-open session with stale feed: block OPEN/ADD as `DATA_STALE`.
- unverified 2028 holiday guard window: block OPEN/ADD as `CALENDAR_UNVERIFIED`.
- delayed bar received during calendar-closed interval: remain closed.
- valid plan generated before close but executed after close-only/closed transition: reject risk-increasing execution.

## Test Requirements

Tests must be written before implementation behavior (TDD) and must cover at least:

- normal Monday-Thursday maintenance close and reopen;
- normal Friday 16:00 CT weekend close;
- Sunday 17:00 CT reopen;
- DST transitions through `America/Chicago`;
- 15:55 ET Simple Paper close-only boundary independent of CME close;
- FLAT after close-only cannot reopen from a late valid plan;
- OPEN position can still EXIT/STOP after new-risk gate closes;
- three fresh consecutive 1-minute bars required after reopen;
- stale feed while calendar says OPEN yields `DATA_STALE`, not `MARKET_CLOSED`;
- delayed bars while calendar says CLOSED cannot create fills;
- each verified 2026/2027 holiday override boundary;
- 2028 guard windows yield `CALENDAR_UNVERIFIED` and prohibit new risk;
- valid Azure snapshot preferred over equal/older packaged snapshot according to version rule;
- Azure outage falls back to packaged snapshot;
- hash-invalid Azure snapshot is rejected;
- no valid snapshot blocks new risk;
- deployed Azure mirror digest equals the committed canonical digest.

## Rollout

Rollout remains PAPER-only until session-gate behavior has passed deterministic unit tests and a live weekend/reopen observation cycle.

Recommended rollout sequence:

1. Land schema, canonical snapshot, loader, and pure state evaluator without changing admission behavior.
2. Land tests and hard new-risk gate in Simple Paper execution.
3. Add observability fields.
4. Package snapshot into `trading-live` and mirror it to Azure Blob with digest verification.
5. Observe a daily maintenance close/reopen in PAPER.
6. Observe a Friday close and Sunday warm-up in PAPER.
7. Only then consider reusing the same gate for any future live-trading adapter.

## Non-Goals

- No online CME lookup inside a 15-second execution cycle.
- No inference that market is closed solely because bars stopped arriving.
- No automatic promotion of unverified 2028 candidate dates to verified trading hours.
- No strategy-specific holiday prediction.
- No separate calendar copies maintained manually in GitHub and Azure.

## Design Decision

Adopt **GitHub canonical + Azure Blob mirror + packaged local fallback**. The canonical committed artifact controls what may be deployed. Azure is a convenience/control-plane mirror, not the sole authority. Runtime uses calendar state to determine expected exchange availability, live bars to determine feed health, and a hard exposure gate to ensure no OPEN/ADD can occur in close-only, closed, stale, warming, or unverified states.
