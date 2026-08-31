# Private adapter migration

The first extraction wave moves reusable behavior from the active private draft PRs into this package:

- `trading-live` PR #12: deterministic RiskGateway/PaperBroker -> `trading_core.paper_execution`
- `trading-live` PR #14: observability domain/projection -> `trading_core.observability`
- `trading-live` PR #11: event identity/plan validation/reservation -> `trading_core.event_orchestrator`
- `trading-runtime` PR #6: runtime activity/dashboard contracts/reducer -> `trading_core.runtime_activity` and `trading_core.dashboard`

Private branches should pin an exact public-core commit and retain compatibility wrappers while existing consumers are migrated. Cloud storage, GitHub network calls, Azure Function routes, deployment workflows, and operational runtime data stay private.
