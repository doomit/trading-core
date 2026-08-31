# Final Private-Core Boundary Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Finish moving reusable trading code/tests into public `trading-core`, leave private repos as runtime/Azure shells, and update every scheduler/Brain instruction to the new architecture.

**Architecture:** Public core owns deterministic logic and normal tests. `trading-live` imports core through its pinned package and keeps only Azure/cloud/GitHub adapters plus explicit Azure-operation workflows. `trading-runtime` is data/mailbox/audit only. Azure owns runtime bus monitoring/pickup.

**Tech Stack:** Python 3.12, pytest, GitHub Actions public Core CI, Azure Functions adapters, GitHub runtime bus, YAML Agent Hub scheduler configuration.

**Spec:** `docs/superpowers/specs/2026-08-31-private-core-boundary-design.md`

## Global Constraints

- No secrets, credentials, private endpoints, private Azure resource identifiers, production payload dumps, or Azure/GitHub SDK imports in public core.
- No automatic CI in private repositories.
- Private Actions are reserved for explicit Azure operations.
- Azure owns routine message-bus monitoring and plan pickup.
- Preserve current production behavior; repository migration does not itself deploy Azure.
- Use TDD for migrated behavior: public test must fail before public implementation is added.

---

### Task 1: Migrate ingest/canonicalization core

**Files:**
- Create: `src/trading_core/ingestion.py`
- Create: `tests/test_ingestion_v32.py`
- Later modify private: `trading-live/function/core.py`

**Interfaces:**
- Produces the existing repository-independent V3.2 symbols currently exported by private `function/core.py`, including payload/upload validation, profile/envelope construction, partitions/keys/hashes, canonicalization, and `process_payload`.
- Private Azure adapter continues to call the same logical API through `trading_core.ingestion`.

- [ ] Copy/adapt the existing private `tests/test_core_v32.py` into public core with imports changed from `from core import *` to `from trading_core.ingestion import *`.
- [ ] Push only the test to a `refactor/**` branch and verify Core CI fails because `trading_core.ingestion` does not exist.
- [ ] Copy the credential-free implementation from private `function/core.py` into `src/trading_core/ingestion.py` without Azure SDK/resource-secret dependencies.
- [ ] Verify exact-head public Core CI passes.

### Task 2: Migrate deterministic market feature builder

**Files:**
- Create: `src/trading_core/market_features.py`
- Create: `tests/test_market_features.py`
- Optionally create: `tests/test_market_feature_serialization.py` only for repository-independent serialization behavior.
- Later modify private Feature Builder adapter imports.

**Interfaces:**
- Produces session/date/phase utilities, exact 1m→5m aggregation, QA helpers, EMA/ATR, deterministic hashes, and `build_features` behavior currently implemented in private `feature_builder_v1_1/market_features/core.py`.

- [ ] Copy/adapt the pure Feature Builder tests into public core with `trading_core.market_features` imports.
- [ ] Verify RED on public CI before adding implementation.
- [ ] Migrate the pure module into `src/trading_core/market_features.py`.
- [ ] Verify exact-head Core CI passes.

### Task 3: Thin `trading-live`

**Files:**
- Modify: `function/core.py` into a compatibility re-export from `trading_core.ingestion`, or update all private imports and remove the file if safe.
- Modify: Feature Builder Azure/local adapters to import `trading_core.market_features`.
- Delete migrated duplicate pure implementations and unit/regression tests.
- Keep Azure adapter/deploy/migrate/diagnostic code and explicit Azure-operation workflows.

**Interfaces:**
- Private runtime behavior must resolve through the pinned public `trading-core` dependency.

- [ ] Re-read private main/active branches immediately before mutation.
- [ ] Update the pinned `trading-core` revision only after the public exact-head CI is green.
- [ ] Replace duplicate private pure implementation with thin imports/re-exports.
- [ ] Remove migrated private unit/regression tests; retain only explicit Azure-operation/integration verification material.
- [ ] Verify a normal private branch push starts zero Actions workflows.

### Task 4: Finish `trading-runtime` data-only cleanup

**Files:**
- Inspect `main`, `gpt-runtime`, and active dashboard/runtime branches for residual reusable schemas/validators/tests/workflows.
- Update docs/prompts to reference public core canonical schemas/contracts.

- [ ] Remove remaining duplicate reusable implementation from private runtime branches where safe.
- [ ] Ensure normal private pushes start zero Actions workflows.

### Task 5: Update Agent Hub virtual jobs and physical worker prompts

**Files:**
- Modify: `doomit/agent-operating-hub/config/virtual-jobs.yaml`
- Modify scheduler/policy docs if they hard-code the old private-CI/runtime-poller model.
- Update active physical worker automations A/B/C/D.
- Update active `Trading Brain Event Handler` automation.

**Interfaces:**
- Brain/fallback reads canonical event/plan contract from `doomit/trading-core` and writes only immutable plan data to `doomit/trading-runtime`.
- Azure performs plan pickup/validation/execution.
- Workers route substantive code/test work to core; private Actions only operate Azure.

- [ ] Update fallback instructions to stop referencing removed `trading-runtime/schemas/*`.
- [ ] Update health/research worker guidance to prefer Azure-authored runtime status/mirror evidence and forbid Actions as routine polling/CI.
- [ ] Update Brain event handler prompt to use public core contract paths and the data-only runtime branch.
- [ ] Update A/B/C/D prompts with the repository boundary so future workers do not recreate private CI/tests.

### Task 6: Final verification and durable completion evidence

- [ ] Verify exact-head public Core CI is green.
- [ ] Verify latest normal pushes to `trading-live` and `trading-runtime` have zero automatic workflow runs.
- [ ] Inspect private repo trees to confirm no migrated pure implementation/test duplication remains.
- [ ] Close `trading-core#1` only when every completion criterion in the spec is proven.
- [ ] Record the new architecture in Agent Hub durable state so future workers inherit it.