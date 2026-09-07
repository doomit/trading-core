# Simple Paper Runtime v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PAPER-only MES/MNQ runtime with explicit contracts, authoritative one-minute ingest, one deterministic 15-second execution loop, and one scheduled 15-minute Brain plan generator.

**Architecture:** TradingView writes authoritative one-minute bars to Azure and best-effort mirrors bounded market data to GitHub. Azure owns durable current Position (`FLAT | OPEN`) and mirrors it to GitHub only for Brain input. A single reentrant execution cycle advances paper state every 15 seconds. Scheduled Brain reads exact GitHub paths every 15 minutes and writes one executable `trading_plan_v2` per symbol. Event Brain and distributed queue/state-machine orchestration remain disabled.

**Tech Stack:** Python 3.12, pytest 8, jsonschema 4, Azure Functions, Azure durable storage adapters, GitHub contents API, ChatGPT scheduled tasks.

**Spec:** `docs/superpowers/specs/2026-09-07-simple-paper-runtime-v1-contracts-design.md`

## Global Constraints

- PAPER only; no live-money authorization or live broker integration.
- Symbols are MES and MNQ only for v1.
- Current Position is only `FLAT` or `OPEN`; at most one OPEN position per symbol.
- Closed history is immutable execution/history audit, not a third current-position state.
- Maximum effective quantity per symbol is 6 micros.
- An OPEN position always has durable stop-loss and take-profit.
- Event Brain remains disabled for v1.
- Runtime correctness does not depend on GitHub mirror freshness, dashboard projection state, hidden ChatGPT memory, BAR_READY, or historical event scans.
- All current-state lookups use exact canonical paths; no list/filter/batch resolver is allowed.
- All component boundaries are versioned contracts; unknown or ambiguous executable keywords fail validation.
- Every runtime feature follows TDD: failing behavior test first, verified failure, minimal implementation, verified green.
- Work is sequential. Do not start a later task until the previous task is reviewed and green.

---

### Task 1: Freeze contracts, strategy/control inputs, and boundary semantics

**Files:**
- Create: `src/trading_core/schemas/market_snapshot_v1.schema.json`
- Create: `src/trading_core/schemas/position_state_v1.schema.json`
- Create: `src/trading_core/schemas/execution_rules_v1.schema.json`
- Create: `src/trading_core/schemas/trading_plan_v2.schema.json`
- Create: `src/trading_core/schemas/ingest_log_v1.schema.json`
- Create: `src/trading_core/schemas/plan_observation_log_v1.schema.json`
- Create: `src/trading_core/schemas/execution_cycle_log_v1.schema.json`
- Create: `src/trading_core/schemas/paper_execution_log_v1.schema.json`
- Create: `src/trading_core/schemas/paper_account_state_v1.schema.json`
- Create: `src/trading_core/simple_paper_contracts.py`
- Create: `config/simple-paper/execution-rules-v1.json`
- Create: `docs/contracts/simple-paper-brain-job-v1.md`
- Create: `docs/strategy/simple-paper-v1.md`
- Create focused contract/schema/semantic tests under `tests/test_simple_paper_*.py`.

**Interfaces:**
- Consumes: the approved Simple Paper Runtime v1 design.
- Produces: the only allowed JSON shapes and exact-path/position-binding semantics used by every later task.

**Acceptance:**
- All nine schemas validate representative good fixtures and reject ambiguous/unsafe fixtures.
- `position_state_v1` accepts only FLAT/OPEN current state; closed history stays in immutable execution/history records.
- `trading_plan_v2` binds exactly to FLAT or `position_id + position_version`.
- MARKET order instructions carry no trigger price; LIMIT/STOP carry a numeric trigger price, including entry/add/reduce.
- `HOLD` makes no executable override; `UPDATE` contains a real change; `EXIT` does not carry add/reduce.
- Effective quantity calculation includes optional add and cannot exceed 6.
- Plan generation and pickup latency calculations are deterministic.
- Paper account balance equals starting balance plus realized P&L.
- Paper execution version transitions are deterministic: OPEN creates version 0; later state-changing executions increment exactly once.
- Canonical rules file validates and is PAPER-only.
- Canonical GitHub market/position/plan paths are frozen in the Brain job contract.
- GitHub mirror failure can be represented without undoing authoritative DB/Position success.
- Existing v1 contracts remain untouched.

### Task 2: Simplify webhook ingest and GitHub market mirror

**Goal:** Make DB bar persistence the only correctness requirement of ingest; remove BAR_READY from the new path.

**Consumes:** `market_snapshot_v1`, `ingest_log_v1`.

**Produces:** authoritative one-minute DB bars, append-only ingest logs, and best-effort exact-path GitHub market snapshots.

**Required tests before runtime code:**
- duplicate 20-bar TradingView windows are idempotent;
- DB success + GitHub mirror failure still returns successful ingest and records `github_status=FAILED`;
- latest-bar receive/DB/mirror timestamps produce the expected latency values;
- missing/late bars carried in the next 20-bar window repair DB history without generating duplicate work;
- no `BAR_READY` publication is required for success.

**Runtime acceptance:** synthetic webhook proves DB + log contract + best-effort mirror; existing webhook format `tv_bars_v2` remains accepted.

### Task 3: Implement durable current Position and deterministic paper execution

**Goal:** One durable current Position per symbol is the executable truth; accepted plans become deterministic paper actions without a separate execution state machine.

**Consumes:** `position_state_v1`, `execution_rules_v1`, accepted `trading_plan_v2`, latest OHLCV.

**Produces:** monotonic OPEN-position versions, immutable `paper_execution_log_v1` actions, `paper_account_state_v1`, and best-effort current Position mirror to `runtime/simple-paper/position/SYMBOL/current.json`.

**Required tests before runtime code:**
- FLAT → OPEN with position id/version 0 and durable stop/TP;
- one OPEN position per symbol;
- accepted protection update increments `position_version`;
- add/reduce are one-shot and effective qty never exceeds 6;
- all entry/add/reduce trigger semantics obey explicit MARKET/LIMIT/STOP contract;
- same-bar stop + take-profit uses `ADVERSE_FIRST`;
- action expiry blocks old OPEN/ADD/REDUCE/EXIT while durable protection remains;
- stale-feed >=15 minutes closes PAPER position synthetically at durable stop with `STALE_FEED_FORCED_STOP`;
- recovered historical bars may apply protective exit but never historical entry;
- 15:00 America/Los_Angeles EOD close is deterministic;
- full close first records execution/account result and then current state becomes FLAT;
- replaying the same plan/bar/action is idempotent;
- durable Position success is not rolled back by Position-mirror failure;
- account balance invariant and execution version transition validators hold for every produced action.

### Task 4: Implement the single 15-second execution cycle

**Goal:** Replace multiple runtime monitors/resolvers with one short-lived, reentrant state-advancing loop.

**Consumes:** exact latest plan path, durable current Position, latest DB market, execution rules.

**Produces:** `plan_observation_log_v1`, Position/actions/account state, `paper_execution_log_v1`, and `execution_cycle_log_v1`.

**Required execution order per symbol:**
1. EOD close check. If it closes the symbol, append execution/account state, set current Position FLAT, log/mirror, and stop that symbol for this tick.
2. Exact-path candidate plan pull and plan-observation log.
3. Exact position/version + expiry validation; accept/apply once or ignore/log.
4. Read latest market and enter one unified execution function: OPEN always manages existing position; FLAT may execute a valid OPEN instruction.
5. Write cycle summary. There is no generic later “persist state” stage.

**Failure-isolation tests before runtime code:**
- GitHub plan read failure is logged and does not prevent OPEN position management;
- malformed/expired/mismatched plan is ignored and previous durable protection continues;
- DB market read failure still reaches safe stale/no-price handling rather than silently skipping the position branch;
- repeated/concurrent-looking invocations converge by idempotency even though Timer normally avoids overlap;
- restart with only durable state is sufficient to determine the next action.

### Task 5: Enable the scheduled 15-minute Brain

**Goal:** One stable scheduled job reads explicit GitHub inputs and strategy prompt, then emits contract-valid MES/MNQ plans.

**Consumes exact paths:**
- `doomit/trading-runtime@gpt-runtime:runtime/simple-paper/market/SYMBOL/current.json`
- `doomit/trading-runtime@gpt-runtime:runtime/simple-paper/position/SYMBOL/current.json`
- `doomit/trading-runtime@gpt-runtime:runtime/simple-paper/plan/SYMBOL/current.json` when present
- `doomit/trading-core@main:config/simple-paper/execution-rules-v1.json`
- `doomit/trading-core@main:docs/strategy/simple-paper-v1.md`

**Produces:** one validated `trading_plan_v2` at the exact current plan path per symbol plus immutable Brain run evidence.

**Acceptance:**
- event-triggered Brain remains off;
- strategy changes normally touch only `docs/strategy/simple-paper-v1.md`, not the stable task prompt;
- plan publication is blocked on schema/semantic validation failure;
- one symbol failure does not overwrite the other symbol's last valid plan;
- `analysis_bar_end`, generated time, exact position binding, and later Azure pickup time make latency observable;
- the Hub task is `urgent`; overdue work still outranks it.

### Task 6: Build log-driven watchdog/dashboard

**Goal:** Render system health entirely from logs and durable current state, without creating another control-plane state machine.

**Consumes:** ingest logs, plan-observation logs, paper execution logs, execution-cycle logs, current Position, current Plan, paper account state, Brain run evidence.

**Produces:** read-only status and HTML rendering.

**Required tests before code:**
- feed latency/age;
- GitHub market mirror age;
- Brain analysis/generation latency;
- Azure plan pickup latency;
- latest active position + version + plan;
- latest execution outcome and realized P&L;
- paper account balance;
- explicit degraded/error reason when any stage is stale/failing;
- dashboard failure/write failure cannot affect execution state.

### Task 7: PAPER cutover and delete obsolete orchestration

**Goal:** Make Simple Paper Runtime v1 the only PAPER correctness path after evidence proves it works.

**Acceptance sequence:**
1. synthetic end-to-end: webhook → DB/log/mirror → valid plan fixture → 15-second cycle → Position/execution/account → logs;
2. controlled real MES/MNQ PAPER session validation;
3. disable old BAR_READY/context/deep-input/Event-Brain/plan-scan/event-state/execution-state/dashboard-control dependencies;
4. keep rollback until the simple path runs without manual intervention across the agreed validation window;
5. only then delete obsolete monitors and rollout/salvage workflows.

## Sequential worker rule

Tasks 2–7 are a dependency chain, not a parallel backlog. Only the earliest unfinished task is runnable. Each task must end with evidence (tests, relevant acceptance result, PR status) and explicitly release the next task. Workers must not “help” by starting later tasks while an earlier task is in progress or under review.
