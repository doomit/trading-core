# Runtime Trading Config Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace deploy-time PAPER account/session/strategy/risk tuning with a versioned runtime config bound end-to-end through Brain plans, immutable execution inputs, RiskGateway and Cockpit.

**Architecture:** Public `trading-core` owns parsing, validation, CME 23H session semantics and config-aware deterministic risk policy. Private `trading-live` reads the active GitHub runtime config, freezes it into every execution snapshot, verifies plan/config identity, and exposes it through Cockpit. Scheduled/Event Brain read the same config and bind every plan to its version. Runtime config publication is immutable version + mutable current pointer in `trading-runtime:gpt-runtime`.

**Tech Stack:** Python 3, dataclasses/Decimal/zoneinfo, Azure Functions/Table Storage, existing GitHub App runtime adapter, JSON runtime artifacts, plain HTML/CSS/JS, targeted pytest tests.

**Spec:** `docs/superpowers/specs/2026-08-31-runtime-config-control-plane-design.md`

## Global Constraints

- PAPER only; no live-money connector or authorization.
- Mandatory protective stop remains compiled and non-configurable.
- Real PROD TradingView feed, stale-feed fail-close, reservation hash and exactly-once semantics remain compiled.
- `plan.config_version` must exactly match the active/frozen runtime config used for risk evaluation.
- Runtime config is invalid unless `paper_only=true` and all numeric values stay inside compiled absolute PAPER safety envelopes.
- `CME_INDEX_23H` is open Sunday 17:00 CT through Friday 16:00 CT except daily 16:00-17:00 CT maintenance.
- Private GitHub Actions are not used for ordinary tests or validation.
- Missing/invalid runtime config fails closed; no legacy hardcoded production fallback.

---

### Task 1: Public runtime config contract and CME session policy

**Files:**
- Create: `src/trading_core/runtime_config.py`
- Create: `tests/test_runtime_config.py`
- Modify: `src/trading_core/__init__.py`

**Interfaces:**
- Produces `TradingRuntimeConfig.from_document(document: dict) -> TradingRuntimeConfig`.
- Produces `TradingRuntimeConfig.to_document() -> dict` for snapshot/audit serialization.
- Produces `cme_session_state(now: datetime, config: TradingRuntimeConfig) -> SessionState` with `session_id` and `session_open`.
- Strategy `profile` is a validated identifier string, not a compiled whitelist; changing PA profile names/settings does not require RiskGateway code changes.

- [ ] Write failing tests for the initial $1M/A2 config, malformed/non-PAPER config, unsafe absolute envelopes, target-risk > max-risk, and round-trip serialization.
- [ ] Write failing table tests around Sunday 16:59/17:00 CT, weekday 15:59/16:00/16:59/17:00 CT, Friday 15:59/16:00 CT, Saturday, and correct trade-date session id.
- [ ] Run only `tests/test_runtime_config.py` and verify RED because the module does not exist.
- [ ] Implement immutable dataclasses and strict parsing with Decimal values plus broad compiled PAPER ceilings.
- [ ] Implement deterministic America/Chicago CME 23H session calculation.
- [ ] Export config/session interfaces from `trading_core`.
- [ ] Re-run targeted config tests and verify PASS.

### Task 2: Config-aware RiskGateway and autonomous position sizing contract

**Files:**
- Modify: `src/trading_core/paper_execution.py`
- Modify: `tests/test_paper_execution.py`

**Interfaces:**
- `RiskContext` gains `runtime_config: TradingRuntimeConfig`.
- RiskGateway derives starting equity, daily loss, streak, entry and open-contract limits from `context.runtime_config`.
- Actionable plans require `config_version`, `strategy_profile`, numeric `risk_budget_usd`, positive `position_action.quantity`, mandatory stop and take-profit.
- RiskGateway rejects config mismatch, strategy-profile mismatch, confidence below active config minimum, risk budget above hard max, actual stop-based risk above plan budget/hard max, and `open + requested quantity > max_open_micro_contracts`.

- [ ] Update/create failing tests that remove the legacy authorized-$50k and one-contract assumptions and construct a valid $1M runtime config in `RiskContext`.
- [ ] Add RED tests for exact config-version/profile binding, confidence floor, 20-micro capacity, $500 plan budget/$1000 hard max, daily loss/streak/entry limits and required take-profit.
- [ ] Run targeted paper execution tests and confirm the new expectations fail on legacy constants.
- [ ] Replace production tunable constants with config reads while retaining fixed feed/identity/PAPER invariants.
- [ ] Add risk-budget validation/recalculation and total-open-capacity check.
- [ ] Re-run targeted paper execution tests and verify PASS.

### Task 3: Private runtime config adapter and immutable execution snapshot

**Files:**
- Create: `function/trading_runtime_config.py`
- Create: `tests/test_trading_runtime_config.py`
- Modify: `function/trading_execution_snapshot_builder.py`
- Modify: `function/trading_execution_snapshot_codec.py`
- Modify: `tests/test_trading_execution_snapshot_builder.py`
- Modify/add: `tests/test_trading_execution_snapshot_codec.py`

**Interfaces:**
- `load_active_runtime_config(runtime: GitHubRuntimeFiles) -> tuple[TradingRuntimeConfig, dict]` reads `runtime/config/current.json`, validates it with public core, then reads immutable `runtime/config/<config_version>.json` and requires exact document equality.
- `build_execution_snapshot(..., runtime_config_document: dict)` freezes exact config document under `runtime_config` plus `config_version`.
- `risk_context_from_snapshot()` reconstructs `TradingRuntimeConfig` and attaches it to `RiskContext`.

- [ ] Write RED adapter tests for current/immutable equality, missing immutable twin, malformed/non-PAPER config and changed current pointer.
- [ ] Replace RTH-only snapshot tests with CME 23H session tests driven by parsed config and $1M starting equity.
- [ ] Add RED codec test proving config survives freeze/reconstruction exactly.
- [ ] Implement bounded runtime config loader with no fallback.
- [ ] Refactor account/session snapshot construction to consume `TradingRuntimeConfig`; remove `$50k` and `CME-RTH` production constants.
- [ ] Freeze runtime config in `paper_execution_input_v1` and decode it.
- [ ] Run targeted adapter/builder/codec tests and verify PASS.

### Task 4: Execution monitor active-config enforcement

**Files:**
- Modify: `function/trading_execution_bus_monitor.py`
- Add/modify: `tests/test_trading_execution_bus_monitor.py`

**Interfaces:**
- For each PLAN_READY event, load active config through the runtime adapter before claiming/filling.
- Require candidate `plan.config_version` and `strategy_profile` to match active config before creating/finalizing an input snapshot.
- If a frozen snapshot exists, require its frozen config version to still equal active current config before execution; config switch suppresses/rejects the old plan fail-closed.

- [ ] Write RED tests for missing config, old-plan/new-config mismatch, matching config execution, and frozen-old-config after a pointer switch.
- [ ] Implement config-aware preauthorization and pass validated config into snapshot builder.
- [ ] Construct RiskGateway from the decoded config-aware `RiskContext` without legacy tunable settings.
- [ ] Re-run targeted execution monitor tests and verify PASS.

### Task 5: Cockpit active config and trade-plan visibility

**Files:**
- Modify: `function/trading_cockpit_materializer.py`
- Modify: `function/trading_cockpit_model.py`
- Modify: `function/trading_cockpit.html`
- Modify: `tests/test_trading_cockpit_materializer.py`
- Modify: `tests/test_trading_cockpit_model.py`
- Modify: `tests/test_trading_cockpit_web.py`

**Interfaces:**
- Materializer reads validated current config and includes a compact `config` block in `cockpit_dashboard_v1`.
- Symbol cards expose `config_version`, `strategy_profile`, `setup_family`, `risk_budget_usd`, quantity, stop, target and plan validity from the effective plan.
- HTML renders Active Config/Risk and per-symbol `TRADE PLAN` sections, including blocked/rejection reason when execution timeline provides one.

- [ ] Add RED model/materializer tests for $1M/A2 active config and effective plan fields.
- [ ] Add RED HTML test for `ACTIVE CONFIG`, `TRADE PLAN`, risk budget, stop/target and config version markers.
- [ ] Implement model/materializer fields without exposing credentials or making Cockpit part of execution authorization.
- [ ] Update responsive HTML to render current config and explicit trade-plan details.
- [ ] Run all cockpit targeted tests + `py_compile` and verify PASS.

### Task 6: Scheduled/Event Brain config-driven strategy contract

**Files:**
- Modify: `doomit/agent-operating-hub:config/trading-deep-brain-v1.md` on its feature branch.
- Update Event Brain automation prompt after repository changes are merged.

**Interfaces:**
- Every Brain pass reads and validates `runtime/config/current.json` plus exact immutable twin before analysis.
- Strategy profile/setup families/confidence/risk target come from config, not hardcoded conservative behavior.
- `PA_AGGRESSIVE_A2` allows first EMA/VWAP pullback, H1/L1 continuation, failed-breakout reversal, strong-body continuation and range-edge scalp when supplied evidence supports them.
- Every plan records config/profile/setup/risk/quantity/stop/target metadata.

- [ ] Update Scheduled Deep Brain contract to generic config-first behavior and A2 semantics.
- [ ] Ensure no config or exact immutable twin => safe NO_TRADE/skip and no actionable plan.
- [ ] Update Event Brain prompt with the same config binding and A2 reevaluation rules while preserving bounded catch-up and no-web constraints.
- [ ] Verify all four scheduler workers still point to the shared Deep Brain contract and Event task remains unscheduled webhook-driven.

### Task 7: Initial runtime config publication

**Files:**
- Create: `doomit/trading-runtime:gpt-runtime/runtime/config/cfg_pa_aggressive_a2_1m_20260901_001.json`
- Create/update: `doomit/trading-runtime:gpt-runtime/runtime/config/current.json`

**Interfaces:**
- Both documents are byte/semantic-equivalent valid `trading_runtime_config_v1` documents.
- Initial values are exactly the approved $1M / CME 23H / A2 / $500 target / $1000 max / 20 micros / $5000 daily stop / 4 losses / 30 entries configuration.

- [ ] Publish immutable config while PAPER new-entry gates remain OFF.
- [ ] Publish current pointer/document to the same exact config.
- [ ] Read both back and verify equality/config version.

### Task 8: Merge, pin public core, and safe Azure rollout

**Files:**
- Modify after public merge: `doomit/trading-live:function/requirements.txt` to pin the merged `trading-core` SHA.
- Generate: one Cloud Shell safe deployment/verification script pinned to merged private SHA.

**Interfaces:**
- Public core PR merges first after fresh targeted verification.
- Private live PR pins public core and merges after fresh targeted verification with zero private workflow runs.
- Deployment script fail-closes new entries, deploys exact private SHA, validates Functions/feed/config/Cockpit/23H session/config-aware RiskGateway, publishes/validates config if needed, then explicitly opens PAPER Event dispatch/auto snapshot/execution and turns kill switch OFF only when all checks pass.

- [ ] Fresh-run all public targeted tests; verify zero failures before public PR/merge.
- [ ] Pin merged public SHA in private requirements and fresh-run private targeted tests/static compile.
- [ ] Verify private PR head has zero private workflow runs and changed files are scoped.
- [ ] Merge private and Hub changes.
- [ ] Generate one consolidated Cloud Shell rollout script; no drip-feed diagnostics.
- [ ] After user runs it, require final evidence: active config version correct, CME session state correct, real MES/MNQ 1m fresh, Cockpit config/trade-plan visible, PAPER Event/auto-snapshot/execution ON, position monitor ON, kill switch OFF, live-money still not connected/authorized.
