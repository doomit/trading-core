# Multi-Account Immutable Ledger Plan Amendment: Crash-Safe Outbox Ordering

This file overrides the outbox-order portions of `2026-09-09-multi-account-immutable-ledger.md`.

For Tasks 3 and 5, implement outbox state `COMMITTING | PENDING | COMPLETE`.

The required transition persistence order is:

1. append/verify immutable position event;
2. create/verify outbox as `COMMITTING`;
3. reconcile execution;
4. reconcile account projection;
5. reconcile position projection;
6. change outbox to `PENDING`;
7. attempt bounded GitHub mirror;
8. mark `COMPLETE` only after every required GitHub target is confirmed.

The mirror drain queries only `PENDING` rows. A `COMMITTING` row is resumed only by transition reconciliation, never delivered directly.

Additional required TDD cases:

- crash after event before outbox: position/account remain at before state and retry creates the same receipt;
- crash after `COMMITTING` receipt before execution/account/position: retry resumes without duplicate event or P&L;
- concurrent mirror drain cannot observe/deliver `COMMITTING` receipts;
- only after projection reconciliation does status become `PENDING`;
- `PENDING -> COMPLETE` failure has no effect on Azure trading state.
