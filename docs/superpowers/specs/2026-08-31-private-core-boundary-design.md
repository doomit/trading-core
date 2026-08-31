# Final Public-Core / Private-Runtime Boundary Design

## Goal

Make `doomit/trading-core` the sole home for reusable trading logic, schemas, deterministic behavior, and substantive automated tests. Keep `doomit/trading-live` and `doomit/trading-runtime` private and thin: Azure/cloud adapters plus private runtime data, logs, queues, status, plans, receipts, and operational evidence.

## Repository responsibilities

### `doomit/trading-core` (public)

Own all code that can be developed and verified without private credentials or production-only identifiers, including:

- TradingView payload validation, ingest/canonicalization rules, deterministic hashes, correction policy, and repository-independent processing.
- Market feature construction, data-quality checks, session calculations, deterministic feature hashes, and related regressions.
- Event/plan contracts, plan validation, idempotent reservation behavior, RiskGateway, PaperBroker, observability projections, dashboard reducer/renderer, and public schemas.
- Pure cost-policy math or other reusable deterministic policy if such logic is extracted from private Azure-operation scripts.
- All normal unit, regression, replay, property, schema, and deterministic behavior tests.

Public code must contain no credentials, Function Keys, private endpoints, Azure resource IDs/names that are operationally sensitive, production payload dumps, private GitHub operational metadata, or Azure/GitHub SDK dependencies.

### `doomit/trading-live` (private)

Own only the Azure/runtime shell:

- Azure Functions entrypoints and trigger composition.
- Azure Storage/Table/Queue/Blob adapters and serialization boundaries.
- GitHub runtime-bus adapter used by Azure.
- Deployment, migration, repair, diagnostics, OIDC/permission verification, and one-shot Azure integration/production verification scripts/workflows.
- Private configuration and operational evidence.

No automatic repository CI is permitted. Private GitHub Actions are only for explicit Azure operations; normal push/PR activity must not start build/unit/regression jobs.

Azure itself owns routine runtime-bus monitoring and plan pickup. GitHub Actions never poll the message bus and never validate plans as part of runtime execution.

### `doomit/trading-runtime` (private)

Own only private mailbox/audit/runtime artifacts and thin I/O documentation:

- Trading Feed PR event ingress.
- `gpt-runtime` immutable plans.
- runtime activity/receipts/status/logs/message metadata.
- durable trading memory where intentionally private.

Canonical schemas and validators are read from/pinned to `trading-core`; they are not duplicated here. No automatic CI or runtime polling Actions.

## Runtime flow

1. Azure receives/builds market state and creates a durable event.
2. Azure writes the event to the Trading Feed PR, causing the ChatGPT event handler to run.
3. The Brain validates input/output using canonical `trading-core` contracts and writes exactly one immutable plan to `trading-runtime/gpt-runtime`.
4. Azure's bounded timer monitors only durable `WAITING_FOR_PLAN` rows, reads the exact immutable plan path, and calls the core validation/idempotency path.
5. Azure RiskGateway/PaperBroker adapters execute paper-only behavior and write correlated activity/current state.
6. GitHub remains mailbox/audit storage; it is not the runtime engine.

## Scheduler / virtual-job rules

- `trading_brain_scheduler_fallback` remains a temporary backup Brain path, but must use `trading-core` as the schema/validation source of truth and write only the immutable plan artifact to `trading-runtime`.
- The fallback does not poll Azure or execute the plan; Azure owns pickup after the plan appears.
- `trading_pre_market_health` and other trading health jobs should prefer Azure-authored status/activity/mirror evidence. They may use an explicit Azure diagnostic Action only when direct evidence is insufficient and the job's purpose requires an Azure operation.
- Generic physical workers must treat substantive code/tests as `trading-core` work by default. Private repo mutation is limited to adapters/runtime data/Azure operations.
- The event-triggered Brain prompt must no longer reference removed `trading-runtime/schemas/*`; it must use `trading-core` canonical contracts.

## Migration completion criteria

Migration is complete when all of the following are true:

1. Legacy `trading-live/function/core.py` reusable ingest/canonicalization behavior is owned by a `trading_core` module and covered by public CI.
2. Legacy Feature Builder pure logic is owned by a `trading_core` module and covered by public CI.
3. `trading-live` consumes the public package for those behaviors and no longer owns duplicate pure implementations.
4. Private unit/regression suites for migrated behavior are removed. Only Azure-operation/integration verification artifacts remain private.
5. `trading-runtime` contains no duplicate reusable plan/event/news schema/validator/test implementation.
6. Normal pushes to both private repos start zero automatic GitHub Actions workflows.
7. Active virtual-job registry, physical scheduler prompts, and the Trading Brain event handler point at the new source-of-truth boundaries.
8. Public Core CI passes at the exact migration head.
9. No production Azure deployment is required to claim repository migration complete; deployment/activation of the new Azure adapter composition remains an explicit Azure operation.