# Public Trading Core Architecture Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move reusable trading logic and heavy tests into public `trading-core`, leaving `trading-live` and `trading-runtime` as thin private adapters.

**Architecture:** `trading-core` is a credential-free `src/` Python package with deterministic domain logic. Private repositories depend inward on core and retain only Azure/GitHub/storage/deployment wiring plus light smoke/integration tests. Migration is incremental so current paper-only behavior remains unchanged while duplicated private logic is retired only after adapter verification.

**Tech Stack:** Python 3.12+, pytest, GitHub Actions standard hosted runners.

**Spec:** `docs/superpowers/specs/2026-08-31-public-core-architecture-design.md`

## Global Constraints

- `trading-core` must not contain secrets, credentials, private endpoints, Azure resource identifiers, production account identifiers, private GitHub operational metadata, or production payload dumps.
- `trading-core` must not import Azure or GitHub SDKs.
- Dependency direction is private repos -> `trading-core`, never the reverse.
- Preserve paper-only risk and execution semantics exactly during extraction.
- Heavy pure-logic tests run in public core; private CI remains light.

---

### Task 1: Bootstrap package and public CI

**Files:**
- Create: `pyproject.toml`
- Create: `src/trading_core/__init__.py`
- Create: `.github/workflows/ci.yml`

**Interfaces:**
- Produces: installable package `trading-core` importable as `trading_core`.

- [ ] **Step 1:** Add minimal Python packaging metadata with pytest test configuration and no runtime cloud dependencies.
- [ ] **Step 2:** Add public CI for Python 3.12 running `python -m pip install -e .[test]` and `pytest -q`.
- [ ] **Step 3:** Keep package `__init__` empty except for package documentation/version-independent exports.
- [ ] **Step 4:** Verify CI wiring after the first RED test commit.

### Task 2: TDD-port deterministic paper execution

**Files:**
- Create: `tests/test_paper_execution.py`
- Create: `tests/test_paper_execution_lookahead.py`
- Create: `src/trading_core/paper_execution.py`

**Interfaces:**
- Produces: `AccountState`, `MarketSnapshot`, `RiskContext`, `RiskGateway`, `DeterministicPaperBroker`, `ExecutionLedger`, `canonical_plan_hash`, `execute_reserved_plan` under `trading_core.paper_execution`.

- [ ] **Step 1:** Port PR #12 tests first, changing only imports to `trading_core.paper_execution` and preserving behavior assertions.
- [ ] **Step 2:** Run public CI and confirm RED due to missing `trading_core.paper_execution`.
- [ ] **Step 3:** Port the deterministic implementation without Azure/GitHub imports or credentials.
- [ ] **Step 4:** Run public CI and confirm all paper execution tests pass, including `NEXT_BAR_NOT_OBSERVED` anti-lookahead regression.
- [ ] **Step 5:** Scan the public diff for forbidden identifiers/material.

### Task 3: Extract observability domain and pure projection logic

**Files:**
- Create: `src/trading_core/observability.py`
- Create: `tests/test_observability.py`
- Create: focused projection/reducer tests from private PR #14 that do not touch Azure persistence/routes.

**Interfaces:**
- Produces: runtime activity/current-status domain models and deterministic projection/reducer helpers.
- Does not produce: Azure Table/Blob adapters or HTTP routes.

- [ ] **Step 1:** Identify pure tests and write/port them first against `trading_core.observability`.
- [ ] **Step 2:** Confirm RED before implementation.
- [ ] **Step 3:** Port only domain models/constants/reducers required by those tests.
- [ ] **Step 4:** Confirm public CI green.
- [ ] **Step 5:** Leave `observability_store.py`, `observability_routes.py`, Azure emit/refresh wiring private.

### Task 4: Extract reusable runtime activity/dashboard contracts

**Files:**
- Create: `src/trading_core/runtime_activity.py`
- Create: `src/trading_core/dashboard.py`
- Create: `schemas/runtime_activity_v1.schema.json` if schema is environment-neutral
- Create: `schemas/trading_dashboard_v1.schema.json` if schema is environment-neutral
- Create: corresponding unit/contract tests ported from `trading-runtime` PR #6.

**Interfaces:**
- Produces: deterministic activity validation, dashboard-state building/rendering helpers that consume plain data.
- Does not produce: GitHub branch/ref mutation or private runtime files.

- [ ] **Step 1:** Port contract/reducer tests first and confirm RED.
- [ ] **Step 2:** Port generic schemas after public-safety review.
- [ ] **Step 3:** Port pure validators/builders/renderers.
- [ ] **Step 4:** Confirm public CI green.
- [ ] **Step 5:** Keep operational `runtime-status/*` and GitHub mutation adapters private.

### Task 5: Extract event orchestration state machine

**Files:**
- Create: `src/trading_core/event_orchestrator.py`
- Create: `tests/test_event_orchestrator.py`

**Interfaces:**
- Consumes: plain event/state objects and dependency protocols/callbacks.
- Produces: deterministic state transitions/decisions.
- Does not produce: Azure storage implementation or GitHub network client.

- [ ] **Step 1:** Split PR #11 tests into pure orchestration vs Azure/GitHub adapter tests.
- [ ] **Step 2:** Port pure tests first and confirm RED.
- [ ] **Step 3:** Implement/port only the state-machine logic in core with dependency injection.
- [ ] **Step 4:** Confirm public CI green.

### Task 6: Thin `trading-live` adapters

**Files:**
- Modify PR #12 branch `function/paper_execution.py` to compatibility-import/re-export core execution API or replace with a narrow adapter.
- Modify PR #14 branch observability modules so domain/reducer logic imports from core while Azure store/routes remain private.
- Modify PR #11 branch orchestration module so pure state logic imports from core while Azure/GitHub adapters remain private.
- Reduce duplicated private pure-logic tests to smoke/import/adapter tests.

**Interfaces:**
- Private code must call core APIs without changing production/paper behavior.

- [ ] **Step 1:** Add a pinned/installable dependency path for core appropriate to current deployment packaging.
- [ ] **Step 2:** Change paper execution private module to thin adapter and run focused private tests locally/CI when available.
- [ ] **Step 3:** Change observability private modules to thin adapters and run focused tests.
- [ ] **Step 4:** Change orchestrator private module to thin adapter and run focused tests.
- [ ] **Step 5:** Do not delete private compatibility shims until consumers are proven.

### Task 7: Thin `trading-runtime` control-plane helpers

**Files:**
- Modify PR #6 pure tools/tests to use core runtime activity/dashboard libraries where practical.
- Keep GitHub operational files, plans/activity/status data, workflow adapters, and private control-plane metadata in `trading-runtime`.

- [ ] **Step 1:** Add/pin core dependency for validation/build helpers.
- [ ] **Step 2:** Replace duplicated reusable code with core imports/wrappers.
- [ ] **Step 3:** Reduce private tests to adapter/contract smoke coverage.
- [ ] **Step 4:** Verify no runtime operational data was moved public.

### Task 8: Architecture verification and cleanup

**Files:**
- Update: `README.md`
- Create/update: architecture boundary documentation as needed.

- [ ] **Step 1:** Run/inspect public core CI and require green.
- [ ] **Step 2:** Search public repository for credential/resource/endpoint patterns and inspect every match.
- [ ] **Step 3:** Verify private repos import core in the migrated areas and do not retain duplicated heavy pure-logic suites unnecessarily.
- [ ] **Step 4:** Record any remaining private CI blocker as external evidence rather than weakening tests.
- [ ] **Step 5:** Only then mark the architecture migration complete.
