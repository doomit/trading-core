# Repository boundary

`trading-core` is the public, credential-free domain layer.

Dependency direction is one-way:

- `trading-live` -> `trading-core`
- `trading-runtime` -> `trading-core`

The core package owns deterministic/reusable trading behavior and heavy tests. Private repositories own cloud/network/storage/deployment adapters and operational data.

## Public core owns

- deterministic risk and paper execution
- event identity, plan validation/pickup, idempotent reservation
- observability activity/status domain projection
- runtime activity and dashboard contracts/reducers
- future reusable strategy/signal/backtest/replay libraries

## Private adapters own

- Azure Functions entrypoints
- Azure Table/Queue/Blob/Storage adapters
- GitHub API mutation/client wiring
- OIDC, credentials and environment/resource configuration
- production scheduling/deployment
- operational runtime files and production-only metadata
- light adapter/smoke/integration/E2E tests

## Safety invariant

No core module may depend on Azure/GitHub SDKs or contain credentials, private endpoints, Azure resource identities, production account/customer identifiers, private operational metadata, or production payload dumps. CI enforces common credential/resource signatures and cloud-SDK import boundaries.
