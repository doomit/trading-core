# Simple Paper Runtime v1 — Contract Design

## Goal

Replace the current distributed paper-trading control plane with a small, deterministic system whose components communicate through explicit versioned contracts. Keep PAPER-only safety, idempotency, durable position state, immutable audit, and fail-closed behavior; remove event-chain orchestration complexity.

## Runtime shape

There are exactly three active rhythms in v1:

1. **TradingView webhook ingest** — normally once per minute per symbol. It validates and durably upserts 1-minute bars, writes an ingest log, then best-effort mirrors a bounded current market snapshot to GitHub. Database persistence is authoritative; GitHub mirror failure must not fail a successful ingest.
2. **Execution cycle** — Azure timer every 15 seconds. It is a short-lived, reentrant, deterministic loop. It never waits for Brain. It advances durable state and returns. Whenever durable Position changes, it best-effort mirrors the current Position to GitHub for Brain; mirror failure never rolls back the durable Position change.
3. **Scheduled Brain** — every 15 minutes. Event Brain is disabled in v1. The scheduler reads only GitHub contracts for market snapshot, current position, execution/position rules, previous active plan, and a separately editable strategy prompt; then writes one plan per symbol.

Symbols in scope: `MES` and `MNQ` only. Mode: `PAPER` only.

## Canonical GitHub paths

Runtime mirror/output repository: `doomit/trading-runtime`, branch `gpt-runtime`.

For `SYMBOL` = `MES` or `MNQ`:

- market: `runtime/simple-paper/market/SYMBOL/current.json`
- position: `runtime/simple-paper/position/SYMBOL/current.json`
- latest Brain plan: `runtime/simple-paper/plan/SYMBOL/current.json`
- immutable Brain evidence: `runtime/simple-paper/brain-runs/<run_id>.json`

Stable public control repository: `doomit/trading-core`, branch `main` after the contract PR merges.

- execution rules: `config/simple-paper/execution-rules-v1.json`
- strategy prompt: `docs/strategy/simple-paper-v1.md`
- schemas: `src/trading_core/schemas/*.schema.json`

All consumers use exact path lookup. No list/filter/batch scan is allowed to discover current state.

## Execution-cycle order

For each symbol, one cycle does the following. Each step logs its own outcome. Exceptions are contained per step so a plan-read failure cannot prevent position management.

1. **EOD forced close**. If the configured EOD close condition applies to an OPEN position, close it, persist the resulting position state, log it, best-effort mirror Position, and stop processing that symbol for this cycle.
2. **Pull candidate plan** from the exact latest-plan path for the symbol. Log pull time, candidate plan id, plan generation time, analysis-bar time, generation latency, pickup latency, and current position reference.
3. **Validate/apply candidate plan once**. A candidate is accepted only if its target position reference exactly matches the current position snapshot observed by Brain (`FLAT`, or exact `position_id + position_version`) and the candidate is not expired. Accepted protection is copied into durable Position state. Already-observed plan ids are no-ops. Mismatched or expired plans are ignored and logged; the previous active plan remains active.
4. **Read latest market data and execute the symbol**. There is no separate generic “market freshness gate” and no separate “execute active plan” stage. One execution function handles both OPEN and FLAT states:
   - OPEN: always attempt position management first using the durable Position protection plus any still-valid active-plan action instructions.
   - FLAT: execute a valid candidate OPEN instruction if its condition is met.
   - Market-data age is measured and logged inside this step. Stale data must never block reaching the position-management code path.
5. **Write cycle summary log**. State is persisted as part of each successful state-changing action; there is no later catch-all “persist state” phase.

If upstream reads fail, the cycle must still attempt all later independent steps that can safely run from durable state.

## Position model

At most one OPEN position exists per symbol.

`position_id` identifies one position lifecycle and is human-readable, e.g. `MES-20260907-0907-01`. It remains unchanged from open through close.

`position_version` is a monotonically increasing integer beginning at `0`. It increments whenever executable position state changes, including quantity, average entry, protection, active plan, or status. Timestamps are metadata and are not used as the version.

A CLOSED position is immutable history. A new position may be opened only after the previous position is CLOSED.

The durable Position record is executable truth. Once a plan is accepted, stop-loss/take-profit and any still-valid one-shot add/reduce instructions needed for ongoing position management are copied into Position. Position management must not depend on GitHub or Brain remaining available.

The GitHub Position file is only a Brain-facing mirror. It must carry `updated_at` and exact `position_id + position_version` so a stale Brain plan can be rejected deterministically by Azure.

## Plan model

A plan is a proposal produced by Brain and consumed by Azure. It binds to:

- one symbol;
- one `analysis_bar_end` (the latest one-minute bar used for analysis);
- one exact position reference: either `FLAT`, or `OPEN` with exact `position_id` and `position_version`;
- one `generated_at` timestamp;
- one `action_valid_until` timestamp for non-protective actions.

Plan protection and plan actions have different lifetimes:

- **Protection** (`stop_loss`, `take_profit`) becomes durable Position state when a matching plan is accepted and remains until replaced or position close.
- **Actions** (`OPEN`, one optional `ADD`, one optional `REDUCE`, `EXIT`) expire at `action_valid_until` and must never fire later because price revisited an old level.

V1 supports at most one optional ADD instruction and one optional REDUCE instruction. Total effective quantity is limited by `execution_rules_v1.max_contracts_per_symbol`, initially 6 micros per symbol. No dynamic risk model, portfolio VaR, pyramiding tree, or multi-target tree is in v1.

Plan acceptance never guesses intent. Position mismatch, malformed fields, unknown keywords, or expired actions are rejected/ignored and logged.

## Market snapshot model

The GitHub market snapshot is a bounded rolling 1-minute window for one symbol. It carries:

- exact symbol;
- `updated_at`;
- latest bar end time;
- DB commit time;
- GitHub mirror time;
- ordered OHLCV bars.

The scheduler may derive 5-minute bars and indicators itself from the one-minute window. Correctness must not depend on hidden ChatGPT memory; retained scheduler context may enrich analysis but the scheduled job must be able to run from the GitHub snapshot plus the other explicit contracts.

## Brain inputs

Each 15-minute scheduled Brain run reads exactly these inputs per symbol from the canonical paths above:

1. current `market_snapshot_v1`;
2. current `position_state_v1`;
3. current `execution_rules_v1`;
4. previous active/latest `trading_plan_v2`, if any;
5. `docs/strategy/simple-paper-v1.md`.

Changing strategy should normally require changing only the strategy prompt, not the scheduler task contract.

## Execution rules

Rules shared by Brain and Azure are explicit data rather than hidden prompt text. V1 includes:

- mode `PAPER`;
- symbols `MES`, `MNQ`;
- max quantity per symbol `6`;
- one OPEN position per symbol;
- initial paper account cash `$10,000,000`;
- EOD forced-close at `15:00:00 America/Los_Angeles`;
- stale-feed synthetic forced-stop threshold of 15 minutes for paper simulation;
- adverse-first handling when one 1-minute bar touches both stop and take-profit.

The initial implementation does not add the old risk-management stack back into the hot path.

## Stale market behavior

Market age is always logged inside symbol execution; it is not an upstream gate that can prevent position management.

- FLAT + stale market: do not open a new position.
- OPEN + data age below the configured stale threshold: manage using newly available OHLCV only; do not invent prices.
- OPEN + data age at or above 15 minutes in PAPER v1: create a synthetic close at the durable stop-loss price with reason `STALE_FEED_FORCED_STOP` and `synthetic=true`.
- Recovered historical bars may repair/replay protective exits, but must never create a historical entry.

This is a paper-simulation rule and is intentionally replaceable when broker-side protective orders exist.

## Logging contracts

Logs are append-only machine-readable observations used by watchdog/dashboard; they are not a second state machine.

Required log families:

- **Ingest log**: symbol, latest bar, request receive time, DB commit time, GitHub mirror time/status, bar counts, calculated latencies.
- **Plan observation log**: pull time, candidate plan timestamps, current position reference, calculated Brain-generation/pickup latencies, and outcome (`ACCEPTED`, `NO_NEW_PLAN`, `ALREADY_OBSERVED`, `IGNORED_POSITION_MISMATCH`, `IGNORED_EXPIRED`, `READ_FAILED`, `INVALID`).
- **Execution-cycle log**: cycle time, symbol, latest market time/age, starting and ending position references, action/outcome, error summary, and EOD/stale-feed flags.

Every step writes its own observation; the final cycle log is a summary, not the only log. Dashboard/watchdog reads logs and durable state directly. Dashboard output never controls trading.

## Component contracts to freeze before runtime work

The first implementation phase defines and tests these contracts in `doomit/trading-core`:

- `market_snapshot_v1`
- `position_state_v1`
- `execution_rules_v1`
- `trading_plan_v2`
- `ingest_log_v1`
- `plan_observation_log_v1`
- `execution_cycle_log_v1`
- exact plan/position matching and plan-latency semantics
- canonical `execution-rules-v1.json`
- scheduled Brain job contract document

No Azure runtime component is modified until these contracts and boundary tests pass review.

## Acceptance principles

- All schemas reject unknown/ambiguous executable keywords by default.
- Every example fixture validates against its schema.
- Boundary tests cover FLAT/open position binding, stale position versions, action expiry, 0–6 quantity bounds, required protection, one add/reduce maximum, timestamps, log latency fields, and PAPER-only mode.
- Exact-path contracts remove resolver/list-scan ambiguity.
- Existing v1 contracts remain untouched during this phase.
- Complexity rule: if a contract requires another runtime state machine to interpret it, redesign the contract instead of adding orchestration.
