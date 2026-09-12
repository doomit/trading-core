# Multi-Account Immutable Execution Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add account-scoped PAPER execution history with immutable Azure position events, retryable GitHub evidence mirroring, exact per-trade P&L, and backward-compatible `simple-paper-v1` operation.

**Architecture:** `trading-core` deterministically derives versioned account/position/execution contracts, one immutable semantic position event per state-changing transition, and trade-ledger projections. `trading-live` persists that event first in Azure, reconciles execution/account/position projections idempotently, and uses a durable Azure outbox to mirror create-only event/execution evidence plus mutable account-scoped snapshots to `trading-runtime`. Existing PAPER deployment gates remain unchanged; no live/prop broker route is introduced.

**Tech Stack:** Python 3.12, pytest 8, jsonschema 4, Azure Functions, `azure-data-tables>=12.7`, GitHub Contents API, existing stage/cutover workflows.

**Spec:** `docs/superpowers/specs/2026-09-09-multi-account-immutable-ledger-design.md`

## Global Constraints

- PAPER only. This implementation must not authorize or connect `PROP` or `LIVE` execution.
- MES and MNQ remain the only actively executed symbols.
- `account_id` must match `[A-Za-z0-9._-]+`.
- Default migrated account is `simple-paper-v1`, `account_type=PAPER`, `broker=INTERNAL_PAPER`, `environment=paper`.
- Every semantic position change is durably represented by exactly one immutable Azure event before current account/position projections mutate.
- Heartbeat-only freshness updates do not create position-history events and do not advance `event_sequence`.
- GitHub is evidence/cache only; Azure remains execution authority.
- GitHub `events/**` and `executions/**` paths are create-only. Existing identical JSON is idempotent success; different JSON is a hard conflict.
- Existing v1 schemas remain readable. New canonical account-scoped state uses v2 schemas rather than silently widening strict v1 contracts.
- Existing historical execution rows are not rewritten.
- Runtime deploy uses only `simple-paper-minimal-stage.yml` followed by `simple-paper-active-cutover.yml` for the exact verified source commit.
- TDD is mandatory: failing test, verified failure, minimal implementation, verified green, commit.

---

### Task 1: Version account-scoped state and execution contracts

**Files:**
- Create: `src/trading_core/schemas/position_state_v2.schema.json`
- Create: `src/trading_core/schemas/paper_account_state_v2.schema.json`
- Create: `src/trading_core/schemas/paper_execution_log_v2.schema.json`
- Modify: `src/trading_core/simple_paper_contracts.py`
- Modify: `src/trading_core/simple_paper_position_execution.py`
- Test: `tests/test_simple_paper_multi_account_contracts.py`
- Test: existing `tests/test_simple_paper_account_execution_contracts.py`
- Test: existing `tests/test_simple_paper_position_lifecycle.py`

**Interfaces:**
- Consumes: existing v1 state/execution semantics.
- Produces:
  - `DEFAULT_PAPER_ACCOUNT_ID = "simple-paper-v1"`
  - `new_paper_account(account_id: str, updated_at: datetime) -> dict[str, Any]`
  - `new_flat_position(symbol: str, updated_at: datetime, *, account_id: str = DEFAULT_PAPER_ACCOUNT_ID) -> dict[str, Any]`
  - v2 execution records containing `account_id`, `account_type`, `broker`, `environment`.

- [ ] **Step 1: Write failing schema tests for independent accounts**

```python
def test_v2_account_and_position_require_account_identity():
    account = paper_account_v2("paper-a")
    position = flat_position_v2("paper-a", "MES")
    _validate("paper_account_state_v2.schema.json", account)
    _validate("position_state_v2.schema.json", position)
    assert account["account_id"] == position["account_id"] == "paper-a"
    assert position["event_sequence"] == 0
    assert position["last_event_id"] is None


def test_v2_account_rejects_live_mode_for_current_runtime():
    value = paper_account_v2("live-one")
    value["account_type"] = "LIVE"
    with pytest.raises(ValidationError):
        _validate("paper_account_state_v2.schema.json", value)
```

- [ ] **Step 2: Run targeted tests and verify RED**

Run: `python -m pytest tests/test_simple_paper_multi_account_contracts.py -q`

Expected: FAIL because v2 schemas/helpers do not exist.

- [ ] **Step 3: Add strict v2 schemas**

`position_state_v2` retains every v1 field and adds required `account_id`, `event_sequence`, `last_event_id`. `paper_account_state_v2` requires `account_id`, `account_type=PAPER`, `broker=INTERNAL_PAPER`, `environment=paper`. `paper_execution_log_v2` retains execution-v1 fields and adds the four account identity fields.

- [ ] **Step 4: Make execution IDs account-domain specific**

Change the internal execution identity helper to include account id in its deterministic source string:

```python
def _execution_id(*, account_id: str, position_id: str, version_after: int,
                  action: str, market_bar_end: str, plan_id: str | None) -> str:
    material = "|".join([
        account_id, position_id, str(version_after), action,
        market_bar_end, plan_id or "-",
    ])
    return f"exec-{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"
```

`_build_execution` copies identity fields from `account`; `execute_flat_plan`, `_add_transition`, `_reduce_transition`, `_close_transition` pass `account["account_id"]`.

- [ ] **Step 5: Run contract and lifecycle tests**

Run: `python -m pytest tests/test_simple_paper_multi_account_contracts.py tests/test_simple_paper_account_execution_contracts.py tests/test_simple_paper_position_lifecycle.py -q`

Expected: PASS, while legacy v1 fixtures remain valid.

- [ ] **Step 6: Commit core v2 contracts**

```bash
git add src/trading_core/schemas src/trading_core/simple_paper_contracts.py src/trading_core/simple_paper_position_execution.py tests/test_simple_paper_multi_account_contracts.py
git commit -m "feat: add account-scoped paper state contracts"
```

### Task 2: Derive immutable semantic position events and trade ledgers in core

**Files:**
- Create: `src/trading_core/schemas/position_event_v1.schema.json`
- Create: `src/trading_core/schemas/trade_ledger_v1.schema.json`
- Create: `src/trading_core/simple_paper_ledger.py`
- Modify: `src/trading_core/simple_paper_position_execution.py`
- Test: `tests/test_simple_paper_position_events.py`
- Test: `tests/test_simple_paper_trade_ledger.py`

**Interfaces:**
- Produces:

```python
def derive_position_event(*, account_before: dict[str, Any],
                          position_before: dict[str, Any],
                          transition: dict[str, Any]) -> dict[str, Any] | None: ...

def validate_position_event_semantics(event: dict[str, Any]) -> None: ...

def project_trade_ledger(events: list[dict[str, Any]]) -> dict[str, Any]: ...
```

- [ ] **Step 1: Write failing OPEN/UPDATE/expiry event tests**

```python
def test_open_event_is_deterministic_and_sequence_one():
    event = derive_position_event(
        account_before=account_before,
        position_before=flat_before,
        transition=open_transition,
    )
    assert event["event_type"] == "POSITION_OPENED"
    assert event["sequence"] == 1
    assert event["position_before"] == flat_before
    assert event["position_after"]["status"] == "OPEN"
    assert event["position_after"]["event_sequence"] == 1
    assert derive_position_event(**same_inputs)["event_id"] == event["event_id"]


def test_protection_only_update_creates_event_without_execution():
    event = derive_position_event(...)
    assert event["event_type"] == "PROTECTION_UPDATED"
    assert event["execution"] is None
    assert event["sequence"] == before["event_sequence"] + 1
```

Also add explicit cases for ADD, REDUCE, EXIT, STOP, TAKE_PROFIT, EOD, stale-feed close, pending-order update and expiry. A transition with only `updated_at` heartbeat must return `None`.

- [ ] **Step 2: Run event tests and verify RED**

Run: `python -m pytest tests/test_simple_paper_position_events.py -q`

Expected: FAIL because ledger module/schema are absent.

- [ ] **Step 3: Implement deterministic event derivation**

Event identity canonical material is:

```python
identity = {
    "account_id": account_after["account_id"],
    "position_id": position_id,
    "sequence": sequence,
    "event_type": event_type,
    "execution_id": execution_id,
    "plan_id": plan_id,
    "position_after": semantic_position_after,
}
event_id = "pevt-" + sha256(canonical_json(identity)).hexdigest()[:32]
```

`position_after.last_event_id` is set to that id and `position_after.event_sequence` to `sequence` before the final event document is returned. For close-to-FLAT, preserve the closing `position_id` in the event even though the current FLAT projection clears it.

- [ ] **Step 4: Write failing trade-ledger projection test**

```python
def test_trade_ledger_sums_partial_reduce_and_exit_pnl():
    ledger = project_trade_ledger([opened, added, reduced, exited])
    assert ledger["account_id"] == "paper-a"
    assert ledger["gross_realized_pnl_usd"] == 125.0
    assert ledger["net_realized_pnl_usd"] == 125.0
    assert ledger["event_ids"] == [e["event_id"] for e in [opened, added, reduced, exited]]
    assert ledger["closed_at"] == exited["occurred_at"]
```

- [ ] **Step 5: Implement trade-ledger projection**

Map execution-bearing events into `entry_fills`, `add_fills`, `reduce_fills`, `exit_fill`; use event `realized_pnl_delta_usd` for exact P&L. For current internal paper simulation set `fees_usd=0.0` and `slippage_usd=0.0`; no market-estimated value is allowed.

- [ ] **Step 6: Run ledger tests and existing execution tests**

Run: `python -m pytest tests/test_simple_paper_position_events.py tests/test_simple_paper_trade_ledger.py tests/test_simple_paper_position_execution.py tests/test_simple_paper_position_lifecycle.py -q`

Expected: PASS.

- [ ] **Step 7: Commit core ledger**

```bash
git add src/trading_core/simple_paper_ledger.py src/trading_core/schemas/position_event_v1.schema.json src/trading_core/schemas/trade_ledger_v1.schema.json src/trading_core/simple_paper_position_execution.py tests/test_simple_paper_position_events.py tests/test_simple_paper_trade_ledger.py
git commit -m "feat: derive immutable paper position events"
```

### Task 3: Make Azure storage account-scoped and add immutable event/outbox persistence

**Repository:** `doomit/trading-live`, branch `feature/multi-account-immutable-ledger`

**Files:**
- Modify: `function/trading_simple_paper_execution_storage.py`
- Create: `tests/test_simple_paper_multi_account_storage.py`
- Extend: `tests/test_simple_paper_execution_storage_adapter.py`

**Interfaces:**
- Produces:

```python
get_position_snapshot(account_id: str, symbol: str) -> tuple[dict | None, str | None]
save_position(account_id: str, position: dict, *, expected_etag: str | None = None) -> bool
get_account(account_id: str) -> dict | None
save_account(account: dict) -> None
execution_exists(account_id: str, execution_id: str) -> bool
append_execution(execution: dict) -> None
get_position_event(account_id: str, event_id: str) -> dict | None
append_position_event(event: dict) -> Literal["CREATED", "EXISTS_MATCH"]
ensure_mirror_outbox(event: dict) -> None
list_pending_mirror(account_id: str, *, limit: int = 10) -> list[dict]
mark_mirror_complete(account_id: str, event_id: str) -> None
mark_mirror_failed(account_id: str, event_id: str, error: str, attempted_at: str) -> None
```

- [ ] **Step 1: Write failing Azure key-isolation tests**

```python
def test_positions_are_keyed_by_account_and_symbol():
    store.save_position("paper-a", position_a)
    store.save_position("paper-b", position_b)
    assert positions.calls[0]["PartitionKey"] == "paper-a"
    assert positions.calls[0]["RowKey"] == "MES"
    assert positions.calls[1]["PartitionKey"] == "paper-b"
```

Also verify account rows use `CURRENT / sha256(account_id)` and execution rows use `account_id / sha256(execution_id)`.

- [ ] **Step 2: Run storage tests and verify RED**

Run: `python -m pytest tests/test_simple_paper_multi_account_storage.py tests/test_simple_paper_execution_storage_adapter.py -q`

Expected: FAIL because existing adapter uses `CURRENT/MES`, fixed account row, and `AUDIT` executions.

- [ ] **Step 3: Implement new Azure tables and keys**

Add table clients:

```python
POSITION_EVENT_TABLE = "SimplePaperPositionEvents"
MIRROR_OUTBOX_TABLE = "SimplePaperGitHubMirrorOutbox"
```

`append_position_event` uses `create_entity`. On `ResourceExistsError`, read the row and compare canonical `DocumentJson`; return `EXISTS_MATCH` only for exact canonical equality, otherwise raise `RuntimeError("position event conflict")`.

- [ ] **Step 4: Implement bounded outbox reads and updates**

`list_pending_mirror` queries only the requested `account_id`, filters `Status eq 'PENDING'`, and stops after `limit`. Attempts are counted; errors are bounded to 512 characters.

- [ ] **Step 5: Run storage tests**

Run: `python -m pytest tests/test_simple_paper_multi_account_storage.py tests/test_simple_paper_execution_storage_adapter.py -q`

Expected: PASS.

- [ ] **Step 6: Commit live storage changes**

```bash
git add function/trading_simple_paper_execution_storage.py tests/test_simple_paper_multi_account_storage.py tests/test_simple_paper_execution_storage_adapter.py
git commit -m "feat: persist account-scoped immutable paper events"
```

### Task 4: Add create-only GitHub evidence writer and deterministic mirror paths

**Repository:** `doomit/trading-live`

**Files:**
- Modify: `function/trading_runtime_files.py`
- Create: `function/trading_simple_paper_ledger_mirror.py`
- Create: `tests/test_trading_runtime_create_only.py`
- Create: `tests/test_simple_paper_ledger_mirror.py`

**Interfaces:**
- Produces:

```python
GitHubRuntimeFiles.create_json(path: str, document: dict, *, message: str) -> dict
account_root(account_id: str) -> str
mirror_event(runtime_files, event: dict) -> None
mirror_execution(runtime_files, execution: dict) -> None
mirror_current(runtime_files, *, account: dict, position: dict, legacy_default: bool) -> None
mirror_trade(runtime_files, trade: dict) -> None
```

- [ ] **Step 1: Write failing create-only collision tests**

```python
def test_create_json_accepts_existing_identical_document(): ...
def test_create_json_rejects_existing_different_document(): ...
def test_create_json_never_sends_sha_for_immutable_path(): ...
```

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_trading_runtime_create_only.py -q`

Expected: FAIL because only `upsert_json` exists.

- [ ] **Step 3: Implement `create_json`**

Behavior:
1. GET path.
2. If absent, PUT without `sha`; require HTTP 201.
3. If present with canonical-equal JSON, return unchanged success.
4. If present with different JSON, raise `RuntimeError("immutable GitHub runtime conflict")`.

- [ ] **Step 4: Implement account-scoped path mapping**

Exact paths:

```text
runtime/simple-paper/accounts/<account_id>/events/<symbol>/<YYYY-MM-DD>/<event_id>.json
runtime/simple-paper/accounts/<account_id>/executions/<symbol>/<YYYY-MM-DD>/<execution_id>.json
runtime/simple-paper/accounts/<account_id>/position/<symbol>/current.json
runtime/simple-paper/accounts/<account_id>/account/current.json
runtime/simple-paper/accounts/<account_id>/trades/<symbol>/<position_id>.json
```

For `simple-paper-v1` only, dual-write legacy `runtime/simple-paper/position/<symbol>/current.json` during this rollout. Do not create legacy immutable history paths.

- [ ] **Step 5: Run mirror tests**

Run: `python -m pytest tests/test_trading_runtime_create_only.py tests/test_simple_paper_ledger_mirror.py tests/test_trading_runtime_branch_isolation.py -q`

Expected: PASS.

- [ ] **Step 6: Commit mirror writer**

```bash
git add function/trading_runtime_files.py function/trading_simple_paper_ledger_mirror.py tests/test_trading_runtime_create_only.py tests/test_simple_paper_ledger_mirror.py
git commit -m "feat: mirror immutable account ledger evidence"
```

### Task 5: Reconcile one transition exactly once and integrate the timer

**Repositories:** `doomit/trading-core`, then `doomit/trading-live`

**Files:**
- Modify core: `src/trading_core/simple_paper_position_execution.py`
- Test core: `tests/test_simple_paper_transition_commit.py`
- Modify live: `function/trading_simple_paper_execution_timer.py`
- Create live: `function/trading_simple_paper_transition_commit.py`
- Create live: `tests/test_simple_paper_transition_reconciliation.py`
- Extend live: `tests/test_simple_paper_execution_timer_apply_once.py`
- Extend live: `tests/test_simple_paper_position_heartbeat.py`

**Interfaces:**
- Core execution functions return a transition containing `position_before` and `account_before` or enough inputs for `derive_position_event`.
- Live produces:

```python
def commit_transition(*, store, runtime_files, event: dict,
                      expected_position_etag: str | None) -> bool: ...

def drain_mirror_outbox(*, store, runtime_files, account_id: str,
                        limit: int = 5) -> int: ...
```

- [ ] **Step 1: Write failing persistence-order and crash-retry tests**

Cases:
- event append raises => no execution/account/position save;
- event exists + execution absent => append execution once and continue;
- event exists + account already equals `account_after` => do not apply P&L again;
- event exists + position already equals `position_after` => do not save again;
- current account/position matches neither before nor after => hard conflict;
- GitHub failure leaves outbox pending and returns Azure transition success.

- [ ] **Step 2: Run reconciliation tests and verify RED**

Run: `python -m pytest tests/test_simple_paper_transition_reconciliation.py -q`

Expected: FAIL because reconciler does not exist.

- [ ] **Step 3: Implement before/after reconciliation**

Use canonical JSON equality for projection comparison. Apply `account_after` only from exact `account_before`. Apply `position_after` only from exact `position_before` with ETag when supplied. Always `ensure_mirror_outbox(event)` before attempting GitHub delivery.

- [ ] **Step 4: Integrate timer with explicit account id**

Add:

```python
_DEFAULT_ACCOUNT_ID = os.environ.get("SIMPLE_PAPER_ACCOUNT_ID", "simple-paper-v1").strip()
```

Validate it against the core account-id rule at startup/cycle entry. Every `get_position`, `get_account`, `execution_exists`, `save_position` call receives account id. `_position_id` includes a short hash of account id so identical plan timestamps across accounts cannot collide.

At the start and end of each symbol cycle call bounded `drain_mirror_outbox(..., limit=5)`. Mirror drain exceptions are logged and contained; they never skip protective position management.

- [ ] **Step 5: Preserve heartbeat semantics**

Heartbeat updates only `updated_at`; it must keep `event_sequence` and `last_event_id` unchanged and must not call `append_position_event`.

- [ ] **Step 6: Run timer/reconciliation regression tests**

Run: `python -m pytest tests/test_simple_paper_transition_reconciliation.py tests/test_simple_paper_execution_timer_apply_once.py tests/test_simple_paper_position_heartbeat.py tests/test_simple_paper_runtime_boundary.py -q`

Expected: PASS and function registration count remains unchanged.

- [ ] **Step 7: Commit reconciler/timer changes**

```bash
git add function/trading_simple_paper_transition_commit.py function/trading_simple_paper_execution_timer.py tests/test_simple_paper_transition_reconciliation.py tests/test_simple_paper_execution_timer_apply_once.py tests/test_simple_paper_position_heartbeat.py
git commit -m "feat: reconcile paper ledger transitions exactly once"
```

### Task 6: Make dashboard/readers account-aware without changing execution authority

**Repository:** `doomit/trading-live`

**Files:**
- Modify: `function/trading_simple_paper_dashboard_storage.py`
- Modify: `function/trading_simple_paper_dashboard_http.py`
- Modify: `function/trading_simple_paper_dashboard_model.py`
- Modify: `tests/test_simple_paper_dashboard_storage.py`
- Modify: `tests/test_simple_paper_dashboard_http.py`
- Modify: `tests/test_simple_paper_dashboard_model.py`

**Interfaces:**
- `SimplePaperDashboardStore.read_snapshot(since_iso: str, account_id: str = "simple-paper-v1")`
- dashboard data response exposes `account_id`, account-scoped executions, and immutable event/trade history counts.

- [ ] **Step 1: Write failing account-filter tests**

Verify dashboard for `paper-a` reads only `PartitionKey='paper-a'` positions/executions/events and never returns `paper-b` records. Default HTTP request remains `simple-paper-v1`; optional `account` query parameter must pass the same safe account-id validation.

- [ ] **Step 2: Run and verify RED**

Run: `python -m pytest tests/test_simple_paper_dashboard_storage.py tests/test_simple_paper_dashboard_http.py tests/test_simple_paper_dashboard_model.py -q`

Expected: current fixed-key tests fail after new account expectations are added.

- [ ] **Step 3: Implement bounded account-scoped dashboard reads**

Add `SimplePaperPositionEvents` and trade-ledger data to the read-only store with existing result caps. Keep market, plan observations and cycle logs symbol-scoped because they are shared market/control evidence; account trading records are filtered by account.

- [ ] **Step 4: Run dashboard tests**

Run: `python -m pytest tests/test_simple_paper_dashboard_storage.py tests/test_simple_paper_dashboard_http.py tests/test_simple_paper_dashboard_model.py -q`

Expected: PASS.

- [ ] **Step 5: Commit dashboard changes**

```bash
git add function/trading_simple_paper_dashboard_storage.py function/trading_simple_paper_dashboard_http.py function/trading_simple_paper_dashboard_model.py tests/test_simple_paper_dashboard_storage.py tests/test_simple_paper_dashboard_http.py tests/test_simple_paper_dashboard_model.py
git commit -m "feat: expose account-scoped paper trade history"
```

### Task 7: Pin the new core and update isolated stage/cutover acceptance

**Repository:** `doomit/trading-live`

**Files:**
- Modify: `function/requirements.txt`
- Modify: `.github/workflows/simple-paper-minimal-stage.yml`
- Modify: `.github/workflows/simple-paper-active-cutover.yml`
- Modify: `tests/test_simple_paper_active_cutover_contract.py`
- Modify: `tests/test_cutover_position_acceptance.py`

**Interfaces:**
- `function/requirements.txt` pins the exact tested `trading-core` commit from Tasks 1–2.
- Stage validates v2 account/current position state for `simple-paper-v1`, verifies new Azure tables, and checks account-scoped GitHub current paths without generating synthetic production trade fills.

- [ ] **Step 1: Run full core suite before pinning**

Run in `trading-core`: `python -m pytest -q`

Expected: all tests PASS. Record exact core commit SHA.

- [ ] **Step 2: Replace the core archive SHA in `function/requirements.txt`**

Use exactly the green core commit SHA; no branch URL and no floating main reference.

- [ ] **Step 3: Write failing stage-contract assertions**

Stage must assert:

```text
SimplePaperPositionEvents table exists
SimplePaperGitHubMirrorOutbox table exists
SimplePaperPositions has simple-paper-v1/MES and simple-paper-v1/MNQ
SimplePaperAccount has CURRENT/sha256(simple-paper-v1)
account-scoped GitHub current position files are present
legacy current position files remain present during compatibility window
all documents remain PAPER-only
```

- [ ] **Step 4: Update workflow acceptance commands**

Replace old `CURRENT/MES`, `CURRENT/MNQ`, `CURRENT/simple-paper-v1`, `AUDIT` assumptions with the new concrete keys. Keep candidate app/storage/runtime branch isolation unchanged.

- [ ] **Step 5: Run all live tests locally/CI-equivalent**

Run: `python -m pytest tests -q`

Expected: PASS.

- [ ] **Step 6: Commit deployment-gate changes**

```bash
git add function/requirements.txt .github/workflows/simple-paper-minimal-stage.yml .github/workflows/simple-paper-active-cutover.yml tests/test_simple_paper_active_cutover_contract.py tests/test_cutover_position_acceptance.py
git commit -m "chore: gate multi-account paper ledger deployment"
```

### Task 8: Stage, cut over, and prove one natural PAPER lifecycle

**Repositories:** `doomit/trading-live`, `doomit/trading-runtime`

- [ ] **Step 1: Run final branch verification**

Core: `python -m pytest -q`

Live: `python -m pytest tests -q`

Expected: both green immediately before deployment request commit.

- [ ] **Step 2: Stage exact live source commit**

Use the existing `ops/simple-paper-minimal-stage-request.json` mechanism so `.github/workflows/simple-paper-minimal-stage.yml` stages the exact commit into isolated Azure resources and a candidate runtime branch.

Acceptance: stage workflow succeeds; v2 state/table/path assertions pass; PAPER-only function registration remains exactly the intended Simple Paper functions.

- [ ] **Step 3: Cut over only that successful candidate**

Use `.github/workflows/simple-paper-active-cutover.yml` with the exact source commit and successful stage run. Do not use a direct deployment or ordinary branch push to activate runtime code.

- [ ] **Step 4: Verify natural production PAPER evidence**

Wait only for naturally arriving TradingView/Brain PAPER activity within the current execution loop; do not inject synthetic production bars. For the first complete position lifecycle after cutover, verify:

```text
Azure PositionEvent count >= 2 for its position_id
first event = POSITION_OPENED
terminal event is EXITED/STOPPED/TARGET_HIT/EOD_CLOSED/STALE_FEED_CLOSED
sequences are 1..N with no gaps/duplicates
execution-bearing events have exactly one matching execution row
account realized_pnl change equals sum(event.realized_pnl_delta_usd)
current position equals terminal event.position_after
GitHub immutable event files match Azure canonical JSON
GitHub immutable execution files match Azure canonical JSON
trade ledger net_realized_pnl_usd equals the Azure event/execution sum
mirror outbox for those events is COMPLETE
```

- [ ] **Step 5: Prove retry safety**

In isolated/non-live-money stage evidence, force one GitHub mirror attempt to fail, confirm Azure event/projection remains durable, then restore mirror access and verify later bounded outbox drain reaches COMPLETE without a second event or double P&L.

- [ ] **Step 6: Record deployment evidence**

Add a review/evidence document under `doomit/trading-live/docs/reviews/` containing exact core/live SHAs, stage run id, cutover run id, position/event ids used for proof, test results, and rollback reference.

## Self-review result

- Spec coverage: account identity, immutable Azure event-first durability, changed-without-fill history, executions, before/after reconciliation, GitHub outbox, create-only mirrors, trade ledger, dashboard filtering, legacy current-path compatibility, PAPER-only safety, and stage/cutover verification are each assigned to a task.
- Placeholder scan: no implementation step depends on an unresolved schema name, key shape, path shape, or function signature.
- Type consistency: canonical v2 state, `position_event_v1`, account-aware execution ids, Azure store signatures, mirror paths, and dashboard account selection are consistent across tasks.
