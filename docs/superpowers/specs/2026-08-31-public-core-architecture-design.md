# Public Trading Core Architecture

## Goal

Move all reusable, deterministic, environment-agnostic trading logic and its heavy test surface into `doomit/trading-core` (public), while reducing `doomit/trading-live` and `doomit/trading-runtime` to thin private adapters for deployment, credentials, environment wiring, durable stores, GitHub/Azure API calls, and production-only metadata.

## Repository boundaries

### `trading-core` (public)

Owns pure/reusable domain behavior:

- trading plan hashing and domain models
- deterministic paper execution
- risk policy and safety gates
- strategy/signal/event decision logic that does not require credentials or production identifiers
- reusable event/state-machine logic
- observability activity/status domain models and pure reducers/projections
- reusable schema/contract validation
- backtest/replay helpers
- unit, regression, property, and replay tests

Core code must be usable without Azure, GitHub, network access, secrets, or production resources.

### `trading-live` (private)

Owns only deployment/runtime adapters:

- Azure Functions entry points
- Azure Storage/Queue/Table/Blob adapters
- GitHub API client wiring used by production
- credentials/identity/OIDC configuration
- environment/resource configuration
- durable exactly-once adapters
- production scheduling and deployment workflows
- smoke/integration/E2E tests that prove adapters connect correctly

No business decision should live here when it can be expressed as a deterministic core function.

### `trading-runtime` (private)

Owns Brain/control-plane operational state:

- Trading Feed PR and Brain request/response wiring
- immutable runtime plan/activity/status files that are operational data
- private runtime metadata and GitHub control-plane operations
- minimal adapter tests

Reusable schemas, validators, reducers, and render/build helpers belong in `trading-core`; runtime keeps only the files/data and adapter invocations that require the private repo.

## Public-safety rule

`trading-core` must never contain:

- secrets, tokens, credentials, connection strings, or private keys
- Azure subscription/resource IDs or resource-specific production names
- private endpoints or authenticated URLs
- production account/customer identifiers
- private GitHub operational metadata
- production payload dumps or logs

Generic literals such as `PROD`, `REAL`, `tradingview`, instrument symbols, risk constants, contract schema names, and synthetic test fixtures are allowed when they are part of domain behavior rather than environment identity.

## Dependency direction

Dependency direction is one-way:

`trading-live` -> `trading-core`

`trading-runtime` -> `trading-core`

`trading-core` must not import either private repository or Azure/GitHub SDKs.

## Packaging

Use a standard Python `src/` package:

- `src/trading_core/...`
- `tests/...`
- `pyproject.toml`

The first migrated module is deterministic paper execution from `trading-live` PR #12. Subsequent migrations extract observability domain/reducer logic from `trading-live` PR #14 and reusable runtime dashboard/contracts from `trading-runtime` PR #6. Event-orchestrator code is split into a pure state machine in core and Azure/GitHub adapters in private repositories.

## CI policy

`trading-core` is the heavy-test repository. Public GitHub Actions runs unit/regression tests on every push/PR using standard hosted runners.

Private repositories keep CI intentionally light:

- import/adapter contract tests
- packaging/smoke tests
- explicit integration/deployment/E2E checks

Full pure-logic test matrices must not be duplicated in private repos after migration.

## Migration safety

Migration is incremental and compatibility-first:

1. Port tests to `trading-core` and prove they fail before implementation.
2. Port minimal implementation and prove public CI green.
3. Change private code to import/use the released or pinned core package/module boundary.
4. Keep temporary compatibility shims when needed to avoid breaking active PRs.
5. Remove duplicated private logic/tests only after the adapter path is green.
6. Do not merge/deploy private production changes merely to complete extraction; preserve current paper-only behavior and exact safety semantics.

## Success criteria

The architecture is complete when:

- deterministic risk/paper-execution behavior lives and tests in `trading-core`
- reusable observability models/reducers/contracts live and test in `trading-core`
- reusable event/state-machine logic lives and tests in `trading-core`
- private repos contain only thin adapters plus light integration/smoke tests for those areas
- public core CI is green
- private adapter CI/smoke checks are green or blocked only by an explicitly documented external quota/platform issue
- no production-sensitive material was introduced into the public repository
