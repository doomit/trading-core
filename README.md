# trading-core

Public, credential-free trading domain library for deterministic strategy/risk/paper-execution logic and reusable runtime contracts.

## Boundary

This repository owns reusable, environment-agnostic trading logic and its heavy test surface. Private repositories (`trading-live`, `trading-runtime`) are thin adapters around this library and retain deployment/runtime-specific configuration, credentials, durable cloud stores, GitHub/Azure API calls, and production-only metadata.

Core currently contains:

- deterministic paper `RiskGateway` and `PaperBroker` behavior
- event identity, plan validation/pickup, and idempotent reservation logic
- observability activity validation and current-status projection
- runtime activity/dashboard schemas, validation, and dashboard reducer

It must not contain secrets, credentials, private endpoints, Azure resource identifiers, production account identifiers, private GitHub operational metadata, or production payload dumps. Core code must not import Azure or GitHub SDKs.

## Development

```bash
python -m pip install -e '.[test]'
pytest -q
```

The public GitHub Actions workflow runs the heavy unit/regression suite. Private repositories should keep only light adapter/smoke/integration/E2E checks for the extracted areas.
