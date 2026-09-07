# Simple Paper Runtime v1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a PAPER-only MES/MNQ runtime with explicit contracts, 1-minute ingest, one deterministic 15-second execution loop, and one scheduled 15-minute Brain plan generator.

**Architecture:** TradingView writes authoritative one-minute bars to Azure and best-effort mirrors a bounded market snapshot to GitHub. A single reentrant Azure execution cycle advances position state every 15 seconds. A scheduled Brain job reads GitHub market/position/rules/strategy inputs every 15 minutes and writes an executable `trading_plan_v2`; Event Brain and distributed queue/state-machine orchestration stay disabled.

**Tech Stack:** Python 3.12, pytest 8, jsonschema 4, Azure Functions, Azure durable storage adapters, GitHub contents API, ChatGPT scheduled tasks.

**Spec:** `docs/superpowers/specs/2026-09-07-simple-paper-runtime-v1-contracts-design.md`

## Global Constraints

- PAPER only; no live-money authorization or live broker integration.
- Symbols are MES and MNQ only for v1.
- At most one OPEN position per symbol.
- Position quantity is an integer from 0 through 6.
- Accepted OPEN position protection requires both stop-loss and take-profit.
- Event Brain remains disabled for v1.
- Runtime correctness does not depend on GitHub mirror freshness, dashboard projection state, hidden ChatGPT memory, BAR_READY, or historical event scans.
- All component boundaries are versioned contracts; unknown executable keywords fail validation.
- Each task is sequential. Do not start a later task until the prior task is reviewed and green.

---

### Task 1: Freeze public contracts and boundary tests

**Files:**
- Create: `src/trading_core/schemas/market_snapshot_v1.schema.json`
- Create: `src/trading_core/schemas/position_state_v1.schema.json`
- Create: `src/trading_core/schemas/execution_rules_v1.schema.json`
- Create: `src/trading_core/schemas/trading_plan_v2.schema.json`
- Create: `src/trading_core/schemas/ingest_log_v1.schema.json`
- Create: `src/trading_core/schemas/plan_observation_log_v1.schema.json`
- Create: `src/trading_core/schemas/execution_cycle_log_v1.schema.json`
- Create: `tests/test_simple_paper_contracts.py`
- Create: `docs/contracts/simple-paper-brain-job-v1.md`

**Interfaces:**
- Consumes: user-approved Simple Paper Runtime v1 design.
- Produces: the only allowed JSON shapes for Azure/GitHub/Brain boundaries used by Tasks 2–6.

- [ ] **Step 1: Write failing contract tests** that load all seven new schemas and assert valid/invalid fixtures for FLAT/open position binding, position version, plan action expiry, max quantity 6, required protection, one optional add/reduce instruction, PAPER-only rules, timestamps, and log latency fields.
- [ ] **Step 2: Open a draft PR and verify CI fails because the new schemas do not exist.** Expected failure is missing schema resource/file, not a syntax/import error.
- [ ] **Step 3: Add the seven minimal JSON Schemas** required by the tests. Use `additionalProperties: false` on executable/state contracts unless a nested metadata object is intentionally free-form.
- [ ] **Step 4: Add the scheduled Brain job contract document** defining exact GitHub inputs, exact output, 15-minute semantics, strategy-prompt separation, and fail-safe behavior.
- [ ] **Step 5: Run PR CI and verify the new tests plus the existing suite pass.**
- [ ] **Step 6: Review the diff for ambiguity and complexity.** Reject any executable field whose meaning cannot be implemented deterministically by Azure without a second resolver/state machine.
- [ ] **Step 7: Mark the PR ready only after CI and review are clean.** Do not merge runtime code in this task.

### Task 2: Implement webhook ingest, latency log, and best-effort GitHub market mirror

**Files:**
- Modify/create in `doomit/trading-live` only after Task 1 contracts merge.
- Add focused tests in `doomit/trading-core` or the existing PR-friendly code-test repository for adapter-independent behavior before runtime changes.

**Interfaces:**
- Consumes: `market_snapshot_v1`, `ingest_log_v1`.
- Produces: authoritative DB bars; append-only ingest observation; GitHub `current` snapshot per symbol.

- [ ] **Step 1: Write failing tests** proving successful DB commit is not rolled back by GitHub mirror failure; duplicate 20-bar webhook windows are idempotent; latest-bar/received/commit/mirror latency values are correct.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement minimal ingest/mirror behavior** without BAR_READY publication in the correctness path.
- [ ] **Step 4: Verify GREEN and existing ingestion tests.**
- [ ] **Step 5: Add one synthetic webhook acceptance test** proving DB state and log contract are correct when GitHub mirror is intentionally failed.

### Task 3: Implement durable Position state and paper execution semantics

**Interfaces:**
- Consumes: `position_state_v1`, `execution_rules_v1`, accepted `trading_plan_v2`, latest OHLCV.
- Produces: monotonic Position versions and deterministic PAPER actions.

- [ ] **Step 1: Write failing tests** for one active position per symbol, OPEN/CLOSED lifecycle, protection copied from accepted plan, adverse-first same-bar stop/TP, one ADD and one REDUCE, quantity cap 6, stale-feed synthetic stop, no historical entry replay, and EOD force close.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement minimal deterministic paper position engine.**
- [ ] **Step 4: Verify GREEN.**
- [ ] **Step 5: Add idempotency tests** showing reprocessing the same plan/bar cannot duplicate an action or position.

### Task 4: Implement the single 15-second execution cycle

**Interfaces:**
- Consumes: latest candidate plan per symbol, durable Position, latest DB market, execution rules.
- Produces: plan-observation logs, position actions/state, execution-cycle logs.

- [ ] **Step 1: Write failing orchestration tests** for the exact order: EOD close; plan pull/observe; candidate validate/apply; market read; unified symbol execution; cycle summary.
- [ ] **Step 2: Add failure-isolation tests** proving plan read/validation exceptions are logged and do not prevent the position-management attempt.
- [ ] **Step 3: Verify RED.**
- [ ] **Step 4: Implement one short-lived reentrant timer handler** with no Brain waiting and no per-stage queue/event chain.
- [ ] **Step 5: Verify GREEN and run the full relevant suite.**
- [ ] **Step 6: Add restart/reentry tests** proving durable state alone determines the next action.

### Task 5: Define and enable the scheduled 15-minute Brain job

**Interfaces:**
- Consumes from GitHub: `market_snapshot_v1`, `position_state_v1`, `execution_rules_v1`, previous plan if present, and strategy prompt.
- Produces: one `trading_plan_v2` for MES and one for MNQ per run.

- [ ] **Step 1: Create the strategy prompt file separately from the stable job contract.**
- [ ] **Step 2: Add fixture-based output validation** requiring every generated plan to pass `trading_plan_v2` before publication.
- [ ] **Step 3: Add the Hub task as urgent scheduled work every 15 minutes** with overdue work retaining higher priority.
- [ ] **Step 4: Keep Event Brain disabled.**
- [ ] **Step 5: Run several PAPER-only scheduled analyses and verify analysis-bar time, plan generation time, position binding, and pickup latency are observable.**

### Task 6: Build log-driven watchdog/dashboard

**Interfaces:**
- Consumes: ingest, plan-observation, execution-cycle logs plus current Position/Plan.
- Produces: read-only system status and HTML rendering.

- [ ] **Step 1: Write failing projection tests** for feed age, GitHub mirror age, Brain generation latency, plan pickup latency, active position/plan, last execution action, and error state.
- [ ] **Step 2: Verify RED.**
- [ ] **Step 3: Implement a read-only dashboard projection.** It must never write trading state or block execution.
- [ ] **Step 4: Verify GREEN and render the existing-style HTML view.**

### Task 7: Cut over and delete obsolete orchestration

**Interfaces:**
- Consumes: fully green Tasks 1–6.
- Produces: Simple Paper Runtime v1 as the only PAPER correctness path.

- [ ] **Step 1: Run an end-to-end PAPER acceptance flow** from synthetic webhook through DB/log/mirror, scheduled plan fixture, 15-second cycle, position execution, and terminal logs.
- [ ] **Step 2: Run controlled real MES/MNQ PAPER sessions** and verify every observed plan/action is explainable from contract-bound inputs.
- [ ] **Step 3: Disable legacy correctness dependencies**: BAR_READY, market-context/deep-input chain, Event Brain, plan list/batch scan, execution/event state split, and dashboard control path.
- [ ] **Step 4: Keep rollback until the simple path runs without manual intervention across the agreed validation window.**
- [ ] **Step 5: Delete obsolete monitors/workflows only after cutover evidence is clean.**
