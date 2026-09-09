# Multi-Account Immutable Execution Ledger Design

Date: 2026-09-09
Status: Approved design, pending implementation plan
Scope: `doomit/trading-core`, `doomit/trading-live`, `doomit/trading-runtime`

## Problem

The current Simple Paper runtime has durable Azure execution audit and current account/position projections, but execution identity and position state are effectively single-account. The account id is fixed to `simple-paper-v1`, current positions are keyed only by symbol, and GitHub mirrors only mutable current position snapshots. This prevents safe multi-account execution and makes historical reconstruction depend on commit diffs or execution rows rather than an explicit immutable position history.

The system must support multiple independent trading accounts while preserving the existing PAPER-only safety boundary and Azure authority.

## Goals

1. Make `account_id` a first-class execution boundary.
2. Persist every semantic position-state change as an immutable Azure event before mutating current projections.
3. Keep existing immutable execution/fill audit and relate it to position events.
4. Mirror immutable events back to GitHub as create-only runtime evidence.
5. Maintain mutable current account/position snapshots only as projections/cache.
6. Produce per-position trade ledgers that allow exact realized P&L reconstruction from durable data.
7. Preserve legacy `simple-paper-v1` behavior during migration.
8. Preserve PAPER-only behavior; this feature must not enable live brokerage execution.

## Non-Goals

- Full event-sourced rebuild of every current projection on every cycle.
- Broker integration for live or prop accounts.
- Strategy changes, sizing changes, or signal-frequency changes.
- Historical backfill of all prior trades unless separately requested.
- GitHub becoming authoritative execution storage.

## Chosen Approach

Use account-scoped immutable position events in addition to the existing immutable execution audit.

Azure remains authoritative. For a semantic transition, persistence order is:

1. Validate transition and derive event identity.
2. Append immutable Azure `PositionEvent`.
3. Append immutable Azure execution/fill record when a fill occurred.
4. Update Azure account projection.
5. Update Azure position projection.
6. Best-effort mirror immutable event to GitHub.
7. Best-effort update GitHub current account/position projections.

If step 2 fails, no current account or position mutation is allowed. GitHub failure must never roll back an already durable Azure transition.

## Account Model

Every execution-domain artifact must carry:

- `account_id`
- `account_type`: `PAPER | PROP | LIVE`
- `broker`
- `environment`

The initial migrated account remains:

- `account_id = simple-paper-v1`
- `account_type = PAPER`
- `broker = INTERNAL_PAPER`
- `environment = paper`

Future accounts may include identifiers such as `paper-aggressive-a2`, `lucid-50k-01`, or `tradovate-live-01`, but adding such accounts does not by itself authorize non-paper execution.

Plans may be shared across accounts. Executions, fills, positions, P&L, account limits, and event streams must never be shared across accounts.

## Azure Storage

### Position Events

Add an immutable table, e.g. `SimplePaperPositionEvents`.

Logical partitioning is account-scoped and symbol/position-aware. Exact physical key shape may optimize Azure Table limits, but uniqueness must include `account_id` and `event_id`.

Required fields:

- `schema = position_event_v1`
- `event_id`
- `account_id`
- `account_type`
- `broker`
- `environment`
- `symbol`
- `position_id`
- `sequence`
- `event_type`
- `occurred_at`
- `plan_id` when applicable
- `execution_id` when applicable
- `before`
- `after`
- `qty_delta`
- `fill_price` when applicable
- `realized_pnl_delta_usd`
- `reason`

`event_id` must be deterministic/idempotent for retries of the same semantic transition.

`sequence` must be monotonic within one `account_id + position_id` stream.

### Existing Executions

Keep `SimplePaperExecutions` immutable. Its logical identity and duplicate checks must become account-aware. The same plan or same symbol lifecycle in two accounts must not collide.

### Current Positions

Current position storage must become account-scoped. A position lookup must require `account_id + symbol` rather than symbol alone.

The current row remains a mutable projection, not historical truth.

### Current Account

Account state storage must support independent rows per account. `get_account` and `save_account` APIs become account-scoped.

## Semantic Position Events

Create events only for meaningful trading-state changes, not heartbeat-only freshness writes.

Minimum event types:

- `POSITION_OPENED`
- `POSITION_ADDED`
- `POSITION_REDUCED`
- `PROTECTION_UPDATED`
- `PENDING_ORDERS_UPDATED`
- `PENDING_ORDERS_EXPIRED`
- `POSITION_EXITED`
- `POSITION_STOPPED`
- `POSITION_TARGET_HIT`
- `POSITION_EOD_CLOSED`
- `POSITION_STALE_FEED_CLOSED`

A transition that changes stop/target/pending orders without a fill must still produce a position event.

A heartbeat that changes only freshness timestamp must not produce a trade-history event.

## Event Generation Boundary

`trading-core` owns deterministic event derivation and validation because canonical contracts/build/tests live there.

`trading-live` owns Azure persistence, GitHub mirroring, timer execution, and migration wiring.

`trading-runtime` remains runtime data only and must not gain validators or execution logic.

## Commit Contract

The current `commit_paper_transition` contract must evolve from execution-centric persistence to transition/event-centric persistence.

A successful semantic transition must satisfy:

- immutable event append succeeds first;
- duplicate retry is idempotent;
- current projections reflect the event exactly once;
- an execution record is appended exactly once when a fill exists;
- GitHub mirrors are best effort and retryable;
- no cross-account dedupe or state contamination occurs.

For changed-without-execution transitions, such as protection updates or pending-order expiry, the event is still mandatory even though no execution record exists.

## GitHub Runtime Layout

New canonical layout:

```text
runtime/simple-paper/accounts/<account_id>/
  account/current.json
  position/<symbol>/current.json
  events/<symbol>/<YYYY-MM-DD>/<event_id>.json
  executions/<symbol>/<YYYY-MM-DD>/<execution_id>.json
  trades/<symbol>/<position_id>.json
```

Rules:

- `events/**` is create-only/immutable.
- `executions/**` is create-only/immutable.
- `account/current.json` is mutable projection.
- `position/<symbol>/current.json` is mutable projection.
- `trades/<symbol>/<position_id>.json` is a derived trade projection finalized/updated from immutable events.
- GitHub content is audit/brain-facing evidence, not execution authority.

Legacy paths may be dual-written temporarily for backward compatibility, but new readers should move to account-scoped paths.

## Trade Ledger Projection

A trade ledger is derived from all immutable events/executions for one `account_id + position_id`.

Minimum trade projection fields:

- `schema = trade_ledger_v1`
- `account_id`
- `symbol`
- `position_id`
- `opening_plan_id`
- `side`
- `opened_at`
- `closed_at`
- `entry_fills[]`
- `add_fills[]`
- `reduce_fills[]`
- `exit_fill`
- `exit_reason`
- `gross_realized_pnl_usd`
- `fees_usd`
- `slippage_usd`
- `net_realized_pnl_usd`
- `duration_ms`
- `event_ids[]`
- `execution_ids[]`

For current internal paper trading, fees/slippage may be zero or explicitly unknown according to the existing simulator contract; they must not be fabricated.

MAE/MFE may be added later and is not required for initial correctness.

## Migration

1. Introduce account-aware APIs with default `simple-paper-v1` compatibility.
2. Create the new Azure event table automatically via existing table initialization pattern.
3. Migrate current account and position reads/writes to account-scoped keys.
4. Dual-write legacy GitHub current position paths during a short compatibility window if current Brain readers still depend on them.
5. Switch Brain/dashboard readers to account-scoped current paths.
6. Remove legacy-path dependency only after tests and production PAPER evidence confirm correctness.

Existing historical execution rows are not rewritten in place.

## Failure Handling

- Azure immutable event append failure: fail closed for that transition; do not mutate current projections.
- Duplicate event: treat as already committed only when deterministic identity and stored content match expected semantic transition.
- Azure execution append failure after event append: transition is incomplete; retry must reconcile from immutable event identity without double-applying projections.
- Azure projection update failure after event append: retry/reconciliation must project the already-recorded event exactly once.
- GitHub event mirror failure: log and retry later; do not undo Azure state.
- GitHub current snapshot failure: log and retry later; Azure remains authoritative.
- Cross-account lookup without explicit account id in new code: reject rather than silently falling back, except at a deliberate legacy compatibility adapter.

## Concurrency and Idempotency

- Position optimistic concurrency remains required.
- Position event identity must be deterministic from account + position + semantic transition identity.
- Execution identity must include the account execution domain.
- Retry after partial persistence must converge to one immutable event and one projection effect.
- Two accounts executing the same plan must create independent events, executions, positions, and P&L.

## Required Tests

### Core contract tests

- same plan + two accounts creates two independent position streams;
- identical plan/action across accounts does not collide;
- position/execution/event semantics require account identity;
- event sequence is monotonic per account + position;
- OPEN creates immutable event;
- ADD creates immutable event;
- REDUCE records `realized_pnl_delta_usd`;
- STOP/TP/EXIT/EOD/stale close creates the corresponding immutable event;
- protection-only UPDATE creates an event without execution;
- pending-order expiry creates an event without execution;
- heartbeat-only refresh creates no semantic event;
- trade ledger reconstruction returns exact realized P&L from executions/events;
- legacy default account behavior remains semantically unchanged.

### Live adapter/storage tests

- Azure event append precedes projection mutation;
- event append failure leaves account and position unchanged;
- retry after event success/projection failure is idempotent;
- duplicate event content mismatch fails loudly;
- GitHub mirror failure does not invalidate durable Azure transition;
- GitHub immutable event path is create-only and never overwritten;
- account-scoped position/account rows do not leak across accounts;
- legacy GitHub current path dual-write works only during compatibility phase.

### Regression/safety tests

- all existing Simple Paper lifecycle tests remain green;
- PAPER-only safeguards remain green;
- no new code path can route to live broker execution;
- MES and MNQ behavior remains isolated inside each account;
- timer-cycle heartbeat/concurrency protections remain intact.

## Deployment Gates

Implementation may deploy only when:

1. New targeted tests pass.
2. Full `trading-core` test suite passes.
3. Relevant `trading-live` tests pass.
4. PAPER-only safety checks pass.
5. A dry-run or non-mutating migration validation confirms existing `simple-paper-v1` state can be read through account-scoped adapters.
6. Deployment is limited to the existing PAPER runtime.
7. Post-deploy evidence shows at least one complete OPEN -> optional ADD/REDUCE/UPDATE -> CLOSE lifecycle with matching Azure event stream and GitHub immutable mirror.
8. Current account/position projections equal the terminal event state.
9. No duplicate event is produced under an intentional retry/replay test.

## Success Criteria

After deployment, an operator can query any account independently and answer exactly:

- how many completed trades occurred;
- every position change and why it happened;
- every fill associated with the position;
- realized P&L per trade and per account;
- the current position projection;
- whether GitHub mirror evidence is complete.

No answer should require reconstructing trade history from mutable `current.json` commit diffs.
