# Multi-Account Immutable Ledger Crash-Safety Amendment

Date: 2026-09-09
Applies to: `2026-09-09-multi-account-immutable-ledger-design.md`
Status: implementation-time correction within approved Approach B

## Problem corrected

The original persistence order created the GitHub mirror outbox only after execution/account/position projections. A process crash after the immutable position event was durable but before outbox creation could advance current state without leaving a durable mirror-delivery obligation.

## Corrected persistence order

For every semantic transition:

1. Derive and validate deterministic `PositionEvent`.
2. Append/verify the immutable Azure event.
3. Immediately create/verify its Azure outbox receipt with `Status=COMMITTING`.
4. Reconcile immutable execution when present.
5. Reconcile account projection from exact `account_before` to `account_after` or accept exact `account_after` as already applied.
6. Reconcile position projection from exact `position_before` to `position_after` using optimistic concurrency, or accept exact `position_after` as already applied.
7. Mark the outbox receipt `PENDING` only after steps 4-6 are complete.
8. A bounded mirror drain processes only `PENDING` receipts, confirms create-only GitHub immutable evidence and required mutable projections, then marks the receipt `COMPLETE`.

`COMMITTING` is never eligible for GitHub delivery. On retry, a matching immutable event plus `COMMITTING` receipt resumes reconciliation from step 4 and eventually becomes `PENDING`. If the process crashes after the event but before the receipt is created, no projection has yet changed, so the next cycle derives the same deterministic event and creates the missing receipt.

## Outbox states

- `COMMITTING`: event is durable; downstream Azure reconciliation is not yet proven complete.
- `PENDING`: Azure execution/account/position reconciliation is complete; GitHub mirror delivery is required.
- `COMPLETE`: all required GitHub targets were confirmed.

No trading-state correctness depends on `PENDING -> COMPLETE` succeeding.
