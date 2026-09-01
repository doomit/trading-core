# Runtime Trading Config Control Plane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace deploy-time PAPER account/session/strategy/risk tuning with a versioned runtime config bound end-to-end through Brain plans, immutable execution inputs, config-aware RiskGateway and Cockpit.

**Architecture:** Public `trading-core` owns parsing, validation, CME 23H session semantics and config-aware deterministic risk policy. Private `trading-live` resolves a small mutable GitHub config pointer to one immutable config, freezes the validated config/hash into every execution snapshot, verifies plan/current/frozen config identity, and exposes it through Cockpit. Scheduled/Event Brain resolve the same pointer and bind every plan to its version. Runtime config publication is immutable version + mutable current pointer in `trading-runtime:gpt-runtime`.

**Tech Stack:** Python 3, dataclasses/Decimal/zoneinfo, Azure Functions/Table Storage, existing GitHub App runtime adapter, JSON runtime artifacts, plain HTML/CSS/JS, targeted pytest tests.

**Spec:** `docs/superpowers/specs/2026-08-31-runtime-config-control-plane-design.md`

## Global Constraints

- PAPER only; no live-money connector or authorization.
- Mandatory protective stop remains compiled and non-configurable.
- New directional plans require take-profit as part of the safe execution contract.
- Real PROD TradingView feed, stale-feed fail-close, reservation hash and exactly-once semantics remain compiled.
- `plan.config_version` must exactly match the active config; frozen config hash/version must still match current before execution.
- Runtime config is invalid unless `paper_only=true` and all numeric values stay inside compiled absolute PAPER safety envelopes.
- `CME_INDEX_23H` is open Sunday 17:00 CT through Friday 16:00 CT except daily 16:00-17:00 CT maintenance.
- Private GitHub Actions are not used for ordinary tests or validation.
- Missing/invalid runtime config fails closed; no legacy hardcoded production fallback.
- `runtime/config/current.json` is a pointer only; immutable full configs live at `runtime/config/<config_version>.json`.

---

### Task 1: Public runtime config contract and CME session policy

**Files:**
- Create: `src/trading_core/runtime_config.py`
- Create: `tests/test_runtime_config.py`

**Interfaces:**
- Produces `TradingRuntimeConfig.from_document(document: dict) -> TradingRuntimeConfig`.
- Produces `TradingRuntimeConfig.to_document() -> dict` for snapshot/audit serialization.
- Produces `cme_session_state(now: datetime, config: TradingRuntimeConfig) -> SessionState` with `session_id` and `session_open`.
- Strategy `profile` is a validated bounded identifier string, not a compiled whitelist; changing profile names/settings does not require RiskGateway code changes.

- [x] Write failing tests for the initial $1M/A2 config, malformed/non-PAPER config, unsafe absolute envelopes, target-risk > max-risk, and round-trip serialization.
- [x] Write failing table tests around Sunday 16:59/17:00 CT, weekday 15:59/16:00/16:59/17:00 CT, Friday 15:59/16:00 CT, Saturday, and correct trade-date session id.
- [x] Verify RED because the module did not exist.
- [x] Implement immutable dataclasses and strict parsing with Decimal values plus broad compiled PAPER ceilings.
- [x] Implement deterministic America/Chicago CME 23H session calculation.
- [x] Re-run public Core CI and verify GREEN.

### Task 2: Config-aware RiskGateway without disturbing proven execution lifecycle

**Files:**
- Create: `src/trading_core/configurable_risk.py`
- Create: `tests/test_configurable_risk.py`
- Leave existing `paper_execution.py` exactly-once/PaperBroker/lifecycle seam intact except only where an interface compatibility fix is strictly required.

**Interfaces:**
- Produces `ConfigurableRiskGateway(config: TradingRuntimeConfig)` implementing the existing `evaluate(...) -> RiskDecision` seam.
- Actionable plans require `config_version`, `strategy_profile`, numeric `risk_budget_usd`, positive `position_action.quantity`, mandatory stop and take-profit.
- Gateway rejects config/profile mismatch, confidence below active config minimum, risk budget above hard max, actual stop-based risk above plan budget/hard max, and `open + requested quantity > max_open_micro_contracts`.

- [x] Add RED tests removing the production dependence on legacy authorized-$50k and one-contract assumptions.
- [x] Add tests for config/profile binding, confidence floor, 20-micro capacity, $500 plan budget/$1000 hard max, daily loss/streak/entry limits and required take-profit.
- [x] Implement gateway by reusing existing immutable execution/order/fill types and deterministic market mechanics.
- [ ] Fresh-run the latest public Core CI head and verify all tests GREEN before merge.

### Task 3: Private active-config pointer resolver

**Files:**
- Create: `function/trading_runtime_config_loader.py`
- Create: `tests/test_trading_runtime_config_loader.py`

**Interfaces:**
- `load_active_runtime_config(runtime) -> ActiveRuntimeConfig` reads `runtime/config/current.json` pointer, requires `schema=trading_runtime_config_pointer_v1`, `paper_only=true`, derives the only allowed immutable path, reads it, verifies version, canonical SHA-256, and parses with public `TradingRuntimeConfig`.
- Produces `ActiveRuntimeConfig(config, config_hash, config_path)`.
- No fallback to legacy constants or App Settings.

- [x] Write RED adapter tests for missing pointer/config, path/version/hash mismatch and non-PAPER pointer.
- [x] Verify RED because loader module did not exist.
- [x] Implement bounded exact pointer resolver.
- [ ] Run private targeted loader test outside private Actions and verify GREEN.

### Task 4: Config-driven execution snapshot and CME risk accounting

**Files:**
- Modify: `function/trading_execution_snapshot_builder.py`
- Modify: `function/trading_execution_snapshot_codec.py`
- Modify: `tests/test_trading_execution_snapshot_builder.py`
- Modify/add: `tests/test_trading_execution_snapshot_codec.py`

**Interfaces:**
- `build_execution_snapshot(..., runtime_config: TradingRuntimeConfig, config_hash: str)` freezes `runtime_config`, `config_version`, `config_sha256` alongside account/session/market state.
- Account starting equity comes from config.
- `entries_this_session` and realized-loss guard use CME trade-session identity, not civil midnight/RTH.
- `session_open/session_id` come from public `cme_session_state`.
- Codec validates frozen config/hash and returns both `RiskContext` and validated `TradingRuntimeConfig` to execution.

- [ ] Replace RTH-only tests with CME 23H config-driven tests and $1M starting equity.
- [ ] Add RED codec tests proving complete config/hash survives freeze/reconstruction and tampering fails closed.
- [ ] Refactor builder to remove production `$50k` and `CME-RTH` constants.
- [ ] Freeze validated config/hash into `paper_execution_input_v1`.
- [ ] Implement config-aware decode/validation.
- [ ] Run targeted builder/codec tests outside private Actions and verify PASS.

### Task 5: Execution monitor active/current/frozen config enforcement

**Files:**
- Modify: `function/trading_execution_bus_monitor.py`
- Add/modify: `tests/test_trading_execution_bus_monitor.py`

**Interfaces:**
- Resolve active config before snapshot/execution.
- Require candidate `plan.config_version` and `strategy_profile` to match active config before claim/fill.
- If a frozen snapshot exists, require frozen config version/hash to still equal the active current config before execution; pointer switch suppresses old plan fail-closed.
- Execute through `ConfigurableRiskGateway(frozen_config)` while preserving existing reservation/exactly-once/PaperBroker lifecycle.

- [ ] Write RED tests for missing config, old-plan/new-config mismatch, matching config execution, and frozen-old-config after pointer switch.
- [ ] Implement config-aware preauthorization and snapshot construction.
- [ ] Replace production `RiskGateway()` instantiation with `ConfigurableRiskGateway(validated_frozen_config)`.
- [ ] Re-run targeted monitor tests and verify PASS.

### Task 6: Cockpit active config and explicit trade-plan visibility

**Files:**
- Modify: `function/trading_cockpit_materializer.py`
- Modify: `function/trading_cockpit_model.py`
- Modify: `function/trading_cockpit.html`
- Modify: `tests/test_trading_cockpit_materializer.py`
- Modify: `tests/test_trading_cockpit_model.py`
- Modify: `tests/test_trading_cockpit_web.py`

**Interfaces:**
- Materializer resolves validated active config and includes a compact `config` block in `cockpit_dashboard_v1`.
- Symbol cards expose `config_version`, `strategy_profile`, `setup_family`, `risk_budget_usd`, quantity, stop, target and plan validity from the effective plan.
- HTML renders `ACTIVE CONFIG` and per-symbol `TRADE PLAN` sections, including execution status/rejection reason when available.

- [ ] Add RED model/materializer tests for $1M/A2 config and effective plan fields.
- [ ] Add RED HTML tests for config/trade-plan/risk/stop/target/version markers.
- [ ] Implement fields without making Cockpit part of authorization.
- [ ] Update responsive HTML.
- [ ] Run all cockpit targeted tests + `py_compile` and verify PASS.

### Task 7: Scheduled/Event Brain config-driven strategy contract

**Files:**
- Modify: `doomit/agent-operating-hub:config/trading-deep-brain-v1.md` on its feature branch.
- Update Event Brain automation prompt after repository changes are ready.

**Interfaces:**
- Every Brain pass resolves `runtime/config/current.json` pointer + exact immutable config/hash before analysis.
- Strategy profile/setup families/confidence/risk target come from config.
- `PA_AGGRESSIVE_A2` allows first EMA/VWAP pullback, H1/L1 continuation, H2/L2, failed-breakout reversal, strong-body continuation and range-edge scalp when supplied evidence supports them.
- Every plan records config/profile/setup/risk/quantity/stop/target metadata.

- [ ] Update Scheduled Deep Brain contract to config-first behavior and A2 semantics.
- [ ] Ensure no valid pointer/immutable config => safe skip/no actionable plan.
- [ ] Update Event Brain prompt with the same binding while preserving bounded catch-up/no-web behavior.
- [ ] Verify all four scheduler workers still use shared Deep Brain contract and Event task remains webhook-driven.

### Task 8: Initial runtime config publication

**Files:**
- Create: `doomit/trading-runtime:gpt-runtime/runtime/config/cfg_pa_aggressive_a2_1m_20260901_001.json`
- Create/update: `doomit/trading-runtime:gpt-runtime/runtime/config/current.json`

**Interfaces:**
- Immutable file is the full valid `trading_runtime_config_v1` document.
- `current.json` is a `trading_runtime_config_pointer_v1` containing exact version/path/canonical SHA-256 and `paper_only=true`.
- Initial values are exactly approved $1M / CME 23H / A2 / $500 target / $1000 max / 20 micros / $5000 session loss stop / 4 losses / 30 entries.

- [ ] Publish immutable config while PAPER new-entry gates remain OFF.
- [ ] Compute canonical config SHA-256 and publish current pointer.
- [ ] Resolve pointer through the same production loader and verify exact config/hash.

### Task 9: Merge, pin public core, and safe Azure rollout

**Files:**
- Modify after public merge: `doomit/trading-live:function/requirements.txt` to pin merged `trading-core` SHA.
- Generate one Cloud Shell safe deployment/verification script pinned to merged private SHA.

**Interfaces:**
- Public core merges first after fresh verification.
- Private live PR pins public core and merges after fresh targeted verification with zero private workflow runs.
- Deployment script fail-closes new entries, deploys exact private SHA, validates Functions/feed/config/Cockpit/23H session/config-aware risk, then explicitly opens PAPER Event dispatch/auto snapshot/execution and turns kill switch OFF only when all checks pass.

- [ ] Fresh-run public targeted/full CI; verify zero failures before public merge.
- [ ] Pin merged public SHA in private requirements and fresh-run private targeted tests/static compile outside private Actions.
- [ ] Verify private PR has zero workflow runs and scoped diff.
- [ ] Merge private and Hub changes.
- [ ] Generate one consolidated Cloud Shell rollout script; no drip-feed diagnostics.
- [ ] After run, require final evidence: active config correct, CME session correct, MES/MNQ fresh, Cockpit config/trade-plan visible, PAPER Event/auto-snapshot/execution ON, position monitor ON, kill switch OFF, live-money still not connected/authorized.
