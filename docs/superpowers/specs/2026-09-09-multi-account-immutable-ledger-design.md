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
4. Reliably mirror immutable events back to GitHub as create-only runtime evidence.
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

1. Validate transition and derive deterministic event identity.
2. Append immutable Azure `PositionEvent` containing enough before/after data to reconcile the transition.
3. Append immutable Azure execution/fill record when a fill occurred.
4. Update Azure account projection.
5. Update Azure position projection.
6. Ensure a durable Azure GitHub-mirror outbox item exists.
7. Attempt create-only GitHub event/execution mirror and mutable current projections.
8. Mark only the outbox delivery state complete after GitHub evidence is confirmed.

If step 2 fails, no current account or position mutation is allowed. If a later Azure step fails, retry starts from the existing immutable event and reconciles downstream state exactly once. GitHub failure never rolls back an already durable Azure transition.

## Account Model

Every execution-domain artifact must carry:

- `account_id`
- `account_type`: `PAPER | PROP | LIVE`
- `broker`
- `environment`

`account_id` must match `[A-Za-z0-9._-]+` so it is safe as an Azure partition key and GitHub path segment.

The initial migrated account remains:

- `account_id = simple-paper-v1`
- `account_type = PAPER`
- `broker = INTERNAL_PAPER`
- `environment = paper`

Future accounts may include identifiers such as `paper-aggressive-a2`, `lucid-50k-01`, or `tradovate-live-01`, but adding such accounts does not by itself authorize non-paper execution.

Plans may be shared across accounts. Executions, fills, positions, P&L, account limits, and event streams must never be shared across accounts.

## Versioned Current-State Contracts

New account-scoped runtime state uses versioned schemas rather than silently widening the existing strict contracts.

### `position_state_v2`

Adds at minimum:

- `account_id`
- `event_sequence`
- `last_event_id`

Opening a new position starts its event sequence at `1`. Every later semantic state event for that position increments it by one. Heartbeat-only freshness changes do not increment the semantic event sequence.

### `paper_account_state_v2`

Adds/standardizes at minimum:

- `account_id`
- `account_type`
- `broker`
- `environment`

The legacy compatibility adapter may continue emitting old v1-shaped GitHub current-state documents temporarily while new canonical account-scoped paths use v2.

## Azure Storage

### Position Events

Add immutable table `SimplePaperPositionEvents`.

Concrete key scheme:

- `PartitionKey = account_id`
- `RowKey = sha256(event_id)`

Store indexed columns for `Symbol`, `PositionId`, `Sequence`, `EventType`, and `OccurredAt` in addition to the canonical `DocumentJson`.

Required event document fields:

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
- `position_before`
- `position_after`
- `account_before`
- `account_after`
- `execution` when applicable
- `qty_delta`
- `fill_price` when applicable
- `realized_pnl_delta_usd`
- `reason`

The event must contain enough data to determine whether downstream account/position/execution projections are still at the before state, already at the after state, or in conflict.

`event_id` must be deterministic/idempotent for retries of the same semantic transition.

`sequence` must be monotonic within one `account_id + position_id` stream.

### Existing Executions

Keep `SimplePaperExecutions` immutable.

Concrete key scheme after migration:

- `PartitionKey = account_id`
- `RowKey = sha256(execution_id)`

`execution_id` derivation must include the account execution domain. The same plan/action in two accounts must generate different execution ids.

### Current Positions

Concrete key scheme:

- `PartitionKey = account_id`
- `RowKey = symbol`

A position lookup in new code requires `account_id + symbol`; symbol-only lookup is allowed only inside the explicit legacy compatibility adapter.

The current row remains a mutable projection, not historical truth.

### Current Accounts

Concrete key scheme:

- `PartitionKey = CURRENT`
- `RowKey = sha256(account_id)`

The entity stores `AccountId` as an indexed/readable field and the canonical account document as `DocumentJson`.

### GitHub Mirror Outbox

Add mutable delivery-state table `SimplePaperGitHubMirrorOutbox`.

Concrete key scheme:

- `PartitionKey = account_id`
- `RowKey = sha256(event_id)`

The outbox is not trading history. It tracks delivery only, with fields such as:

- `EventId`
- `Symbol`
- `TargetPathsJson`
- `Status = PENDING | COMPLETE`
- `AttemptCount`
- `LastAttemptAt`
- `LastError`

Each normal execution timer cycle first/last performs a bounded drain of pending mirror items so a transient GitHub failure is retried without requiring a separate GitHub Action or high-frequency scheduler.

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

`trading-core` owns deterministic event derivation, event-type mapping, state-version semantics, trade-ledger projection, and validation because canonical contracts/build/tests live there.

`trading-live` owns Azure persistence, reconciliation, GitHub mirroring/outbox delivery, timer execution, and migration wiring.

`trading-runtime` remains runtime data only and must not gain validators or execution logic.

## Commit and Reconciliation Contract

The current `commit_paper_transition` contract evolves from execution-centric persistence to transition/event-centric persistence.

For a new semantic transition:

1. Derive the deterministic event from the transition.
2. If the event does not exist, append it immutably.
3. If the event already exists, require byte-equivalent canonical semantics; mismatch is a hard conflict.
4. Reconcile execution record from the event: create if absent, verify if present.
5. Reconcile account projection: apply `account_after` only when current matches `account_before`; accept as complete when current already matches `account_after`; otherwise fail conflict.
6. Reconcile position projection using the same before/after rule and optimistic concurrency.
7. Ensure the GitHub mirror outbox item exists.
8. Attempt mirror delivery.

This rule makes partial persistence retryable without double-applying P&L or position changes.

For changed-without-execution transitions, such as protection updates or pending-order expiry, the event is still mandatory even though no execution record exists.

## GitHub Runtime Layout

New canonical layout on `trading-runtime:gpt-runtime`:

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
- create-only retry verifies existing canonical JSON; different content at an existing immutable path is a hard mirror conflict.
- `account/current.json` is a mutable projection.
- `position/<symbol>/current.json` is a mutable projection.
- `trades/<symbol>/<position_id>.json` is a derived trade projection, mutable while OPEN and finalized at CLOSE.
- GitHub content is audit/brain-facing evidence, not execution authority.

Legacy paths may be dual-written temporarily for backward compatibility, but new readers move to account-scoped paths.

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

For the current internal paper simulator, fees and slippage are `0` only when the configured simulator models are explicitly `NONE`; otherwise they must come from the configured model and must never be fabricated.

MAE/MFE may be added later and is not required for initial correctness.

## Migration

1. Introduce v2 account-aware contracts while retaining explicit legacy adapters.
2. Create the new Azure event and mirror-outbox tables through the existing table initialization pattern.
3. Migrate current account and position reads/writes to the concrete account-scoped keys above.
4. Seed or read-through existing `simple-paper-v1` current state without rewriting historical execution rows.
5. Dual-write legacy GitHub current position paths during a compatibility window if current Brain readers still depend on them.
6. Switch Brain/dashboard readers to account-scoped current paths.
7. Remove legacy-path dependency only after tests and production PAPER evidence confirm correctness.

Existing historical execution rows are not rewritten in place. Historical backfill into new position events is a separate optional migration.

## Failure Handling

- Azure immutable event append failure: fail closed for that transition; do not mutate current projections.
- Duplicate event with matching canonical content: reconcile downstream state and continue idempotently.
- Duplicate event with mismatching content: hard conflict; do not mutate projections.
- Azure execution append failure after event append: retry from event and create/verify execution without generating a second event.
- Azure account projection failure after event append: retry applies `account_after` only from the exact expected before state.
- Azure position projection failure after event append: retry applies `position_after` only from the exact expected before state and with optimistic concurrency.
- Outbox creation failure: event remains authoritative; next retry recreates the missing outbox before declaring mirror delivery complete.
- GitHub immutable mirror failure: outbox stays pending; no Azure trading state is rolled back.
- GitHub current snapshot failure: outbox stays pending until immutable evidence and required current projections are confirmed.
- Cross-account lookup without explicit account id in new code: reject rather than silently falling back, except at the deliberate legacy compatibility adapter.

## Concurrency and Idempotency

- Position optimistic concurrency remains required.
- Position event identity is deterministic from account + position + semantic transition identity.
- Execution identity includes the account execution domain.
- Retry after partial persistence converges to one immutable event, at most one execution record, and one projection effect.
- Before/after reconciliation prevents double P&L application.
- Two accounts executing the same plan create independent events, executions, positions, and P&L.
- Event sequence is validated against the exact prior position state rather than allocated from a global counter.

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
- event contains sufficient before/after state for deterministic reconciliation;
- trade ledger reconstruction returns exact realized P&L from executions/events;
- legacy default account behavior remains semantically unchanged.

### Live adapter/storage tests

- concrete Azure keys isolate accounts;
- Azure event append precedes projection mutation;
- event append failure leaves account and position unchanged;
- retry after event success/execution failure is idempotent;
- retry after event success/account failure is idempotent;
- retry after event success/position failure is idempotent;
- duplicate event content mismatch fails loudly;
- before/after projection mismatch fails conflict instead of overwriting;
- GitHub mirror failure does not invalidate durable Azure transition;
- pending outbox item is retried by later timer cycles;
- GitHub immutable event path is create-only and never overwritten;
- existing identical GitHub immutable JSON is accepted as idempotent success;
- existing different GitHub immutable JSON is a hard conflict;
- account-scoped position/account rows do not leak across accounts;
- legacy GitHub current path dual-write works only during compatibility phase.

### Regression/safety tests

- all existing Simple Paper lifecycle tests remain green;
- PAPER-only safeguards remain green;
- no new code path can route to live broker execution;
- MES and MNQ behavior remains isolated inside each account;
- timer-cycle heartbeat/concurrency protections remain intact;
- mirror-outbox draining is bounded and cannot block protective execution indefinitely.

## Deployment Gates

Implementation may deploy only when:

1. New targeted tests pass.
2. Full `trading-core` test suite passes.
3. Relevant `trading-live` tests pass.
4. PAPER-only safety checks pass.
5. A dry-run/non-mutating migration validation confirms existing `simple-paper-v1` state can be read through account-scoped adapters.
6. Deployment is limited to the existing PAPER runtime.
7. Post-deploy evidence shows at least one complete OPEN -> optional ADD/REDUCE/UPDATE -> CLOSE lifecycle with matching Azure event stream and GitHub immutable mirror.
8. Current account/position projections equal the terminal event state.
9. No duplicate event or double P&L is produced under intentional retry/replay tests.
10. A simulated GitHub mirror failure leaves trading durable in Azure and later drains successfully from the outbox.

## Success Criteria

After deployment, an operator can query any account independently and answer exactly:

- how many completed trades occurred;
- every position change and why it happened;
- every fill associated with the position;
- realized P&L per trade and per account;
- the current position projection;
- whether GitHub mirror evidence is complete.

No answer should require reconstructing trade history from mutable `current.json` commit diffs.
