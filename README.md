# trading-core

Public, credential-free trading domain library and the sole repository for reusable trading code, build logic, schemas, validators, and substantive automated tests.

## Hard boundary

Anything that needs normal build, unit, regression, replay, property, schema, strategy, risk, paper-execution, reducer, or deterministic behavior testing belongs here. Private repositories (`trading-live`, `trading-runtime`) are runtime/operations shells only.

Core currently contains:

- deterministic paper `RiskGateway` and `PaperBroker` behavior
- event identity, plan validation/pickup, and idempotent reservation logic
- trading plan/event/news runtime contracts and news freshness validation
- observability activity validation and current-status projection
- runtime activity/dashboard schemas, validation, reducer, and renderer

Core must not contain secrets, credentials, private endpoints, Azure resource identifiers, production account identifiers, private GitHub operational metadata, production payload dumps, or cloud-specific SDK dependencies. Core code must not import Azure or GitHub SDKs.

## Private repository rule

`trading-live` may contain Azure Functions entrypoints, Azure Storage/Table/Queue/Blob adapters, deployment/migration/repair/diagnostic scripts, private configuration and operational evidence. `trading-runtime` may contain private mailbox/audit data such as Trading Feed messages, immutable plans, receipts, status, logs, and runtime metadata.

Private GitHub Actions are not CI runners and are not runtime pollers. They are reserved for explicit Azure operations such as deploy, migration, resource preparation, OIDC/permission checks, repair, diagnostics, and one-shot Azure integration/production verification. Routine message-bus monitoring and plan pickup are owned by Azure itself.

## Development

```bash
python -m pip install -e '.[test]'
pytest -q
```

The public GitHub Actions workflow owns the heavy build/unit/regression test surface.
