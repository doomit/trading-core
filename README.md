# trading-core

Public, credential-free trading domain library for deterministic strategy/risk/paper-execution logic and reusable runtime contracts.

## Boundary

This repository may contain only reusable, environment-agnostic code and tests. It must not contain secrets, credentials, private endpoints, Azure resource identifiers, production account identifiers, private GitHub operational metadata, or production payload dumps.

Private repositories (`trading-live`, `trading-runtime`) are thin adapters around this library and retain deployment/runtime-specific configuration and credentials.
