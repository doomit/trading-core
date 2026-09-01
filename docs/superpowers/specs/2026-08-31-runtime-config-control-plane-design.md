# Runtime Trading Config Control Plane Design

## Goal

Make PAPER account sizing, CME session behavior, PA strategy profile, confidence/action thresholds, and tunable risk limits changeable by publishing a new runtime config instead of deploying code. Keep non-negotiable safety invariants in code.

## Authoritative configuration

`doomit/trading-runtime:gpt-runtime` is the runtime configuration/audit source. A valid config is published as:

- immutable `runtime/config/<config_version>.json` containing the full `trading_runtime_config_v1` document;
- mutable `runtime/config/current.json` containing only a validated pointer to that exact immutable document.

The pointer contract is:

```json
{
  "schema": "trading_runtime_config_pointer_v1",
  "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
  "config_path": "runtime/config/cfg_pa_aggressive_a2_1m_20260901_001.json",
  "config_sha256": "<canonical-json-sha256>",
  "paper_only": true
}
```

Consumers must read `current.json`, derive the only allowed immutable path from `config_version`, read that document, validate it with public `trading-core`, and verify its canonical SHA-256 exactly matches `config_sha256`. Missing, malformed, mismatched, or non-PAPER pointer/config state fails closed for new entries.

The first active config is `cfg_pa_aggressive_a2_1m_20260901_001` and uses:

- `paper_only: true`
- starting equity `$1,000,000`
- session profile `CME_INDEX_23H`
- strategy profile `PA_AGGRESSIVE_A2`
- default analysis timeframe `5m`
- minimum actionable confidence `0.60`
- target risk per trade `$500`
- hard max risk per trade `$1,000`
- max open micro contracts `20`
- max daily realized loss `$5,000`
- max consecutive losses `4`
- max entries per CME session `30`

Changing any of these values later requires only creating a new valid immutable config and atomically switching the small `runtime/config/current.json` pointer; no code deployment or Function App setting change is required.

## Config contract

Public `trading-core` owns `trading_runtime_config_v1` parsing/validation and safety envelopes. The immutable runtime document contains:

```json
{
  "schema": "trading_runtime_config_v1",
  "config_version": "cfg_pa_aggressive_a2_1m_20260901_001",
  "created_at": "2026-09-01T05:00:00Z",
  "paper_only": true,
  "account": {
    "starting_equity_usd": 1000000
  },
  "session": {
    "profile": "CME_INDEX_23H",
    "timezone": "America/Chicago",
    "maintenance_start": "16:00",
    "maintenance_end": "17:00"
  },
  "strategy": {
    "profile": "PA_AGGRESSIVE_A2",
    "analysis_timeframe": "5m",
    "min_action_confidence": 0.60,
    "setup_families": [
      "EMA_VWAP_FIRST_PULLBACK",
      "H1_L1_TREND_CONTINUATION",
      "H2_L2_TREND_CONTINUATION",
      "FAILED_BREAKOUT_REVERSAL",
      "STRONG_BODY_CONTINUATION",
      "RANGE_EDGE_SCALP"
    ]
  },
  "risk": {
    "target_risk_per_trade_usd": 500,
    "max_risk_per_trade_usd": 1000,
    "max_open_micro_contracts": 20,
    "max_daily_realized_loss_usd": 5000,
    "max_consecutive_losses": 4,
    "max_entries_per_session": 30
  }
}
```

Validation rejects unknown schema/session-profile values, non-PAPER mode, malformed timezone-aware timestamps, non-positive money/count limits, `target_risk_per_trade_usd > max_risk_per_trade_usd`, and unsafe values outside compiled absolute envelopes. Strategy profile/setup identifiers are validated as bounded identifiers so future research profiles can be introduced by config without a RiskGateway deploy.

## Code-level safety invariants

The following remain compiled and cannot be relaxed by config:

- PAPER-only account mode; live-money trading remains unsupported and unauthorized.
- Real `TradingView` PROD feed only.
- Stale/future feed fail-closed.
- Mandatory protective stop for every LONG/SHORT plan.
- Mandatory take-profit for every new LONG/SHORT entry in this runtime profile family.
- Exact immutable plan identity and reservation hash.
- Exactly-once-in-effect execution.
- Positive quantity and supported MES/MNQ symbols only.
- Deterministic session closure during CME maintenance/weekend.
- A plan can execute only when `plan.config_version` exactly matches the active runtime config frozen for execution and the current pointer has not switched to another config before execution.
- A plan's requested risk budget cannot exceed configured hard max risk, and actual stop-based risk cannot exceed either the plan budget or configured hard max.

Absolute compiled envelopes for the configuration layer are deliberately broad enough for PAPER research but finite: starting equity <= $10,000,000; max risk/trade <= $25,000; max open micros <= 100; daily loss cap <= $100,000; max consecutive losses <= 20; max entries/session <= 200.

## CME 23-hour session

`CME_INDEX_23H` follows America/Chicago local time:

- Sunday 17:00 CT through Friday 16:00 CT is the weekly trading window.
- Each trading day is open from 17:00 CT to the following 16:00 CT.
- 16:00-17:00 CT is maintenance and rejects new entries.
- Friday 16:00 CT through Sunday 17:00 CT rejects new entries.
- A session id belongs to the trade date ending at 16:00 CT, e.g. Sunday 17:00 CT through Monday 16:00 CT is `CME-2026-09-01`.

Existing open PAPER positions remain protected by the independent position lifecycle monitor during closed-entry windows.

For risk accounting, `entries_this_session` and the configured daily/session realized-loss guard use this CME trading-session identity rather than resetting at civil midnight.

## Plan/config binding and position sizing

Every Scheduled Deep Brain and Event Brain actionable or NO_TRADE plan records:

- `config_version`
- `strategy_profile`
- `setup_family` when applicable
- `risk_budget_usd` for LONG/SHORT
- `position_action.quantity`, protective stop, take-profit for LONG/SHORT

The Brain decides quantity from setup quality, stop distance, volatility, current account/risk state and the config target budget. RiskGateway independently recalculates actual risk from the observed next-bar fill, stop distance, point value, quantity and commission. It rejects if quantity exceeds configured remaining open-contract capacity, if actual risk exceeds the plan budget, or if plan budget/actual risk exceeds the configured hard max.

No fixed one-contract limit remains in production policy.

## Execution snapshot

The private Azure adapter resolves `runtime/config/current.json` to its exact immutable config before freezing an execution input. The frozen `paper_execution_input_v1` includes the complete validated runtime config, config version, and verified config hash. The codec reconstructs the public-core `RiskContext` and validated runtime config from that frozen snapshot.

Immediately before execution, Azure also resolves the current pointer again. If current version/hash differs from the frozen version/hash or the plan's `config_version`, the old plan is suppressed/fails closed and a new Brain decision is required. Missing/malformed config prevents snapshot creation and therefore prevents execution.

This makes each decision auditable and prevents an in-flight plan from mixing old Brain parameters with a newer risk configuration.

## Brain behavior

Scheduled Deep Brain and Event Brain must resolve the active immutable config before analyzing. `PA_AGGRESSIVE_A2` is a short-horizon 5m PA research profile intended to generate PAPER samples more readily than conservative v1:

- first EMA20/VWAP pullback rejection may be actionable in a clear trend;
- H1/L1 continuation is permitted when trend/location/bar quality align;
- H2/L2 remains supported;
- failed breakout close back through a supplied OR/swing/range level may reverse without waiting for a second confirmation;
- strong-body continuation is allowed when aligned with EMA/VWAP and not clearly exhausted;
- range-edge rejection/failed breakout scalps are allowed toward range mid/next structure;
- confidence at or above config `min_action_confidence` may be actionable;
- every directional plan still requires structural invalidation, stop, target, risk budget and quantity.

The profile is a research configuration, not a claim of edge. Future profile names/setup-family lists can be added by publishing config and updating Brain guidance stored in runtime/strategy guidance when needed; the deterministic RiskGateway does not require a deploy merely to change the active profile identifier or tunable risk numbers.

## Cockpit

The materialized dashboard resolves active config and shows:

- active config version/profile;
- starting PAPER equity;
- CME session open/maintenance/closed status;
- risk target/hard cap, max contracts, daily stop, loss-streak stop and entry cap;
- per-symbol current effective trade plan: BASELINE/OVERRIDE, decision, confidence, setup family, quantity, risk budget, stop, target, validity, strategy profile and config version;
- execution status/rejection reason when available.

Every historical trade remains linked to the immutable plan/config version used for that trade.

## Rollout

Implementation occurs on feature branches with targeted tests outside private GitHub Actions. Public core merges first and private `trading-live` pins the merged public SHA. Agent Hub Deep Brain contract and Event Brain task are then updated to resolve runtime config. Finally the initial immutable config plus pointer are published to `trading-runtime:gpt-runtime`, private runtime is safely deployed with new-entry gates fail-closed, real feed/config/cockpit are verified, and PAPER entry gates are opened only after config-aware E2E checks pass.
