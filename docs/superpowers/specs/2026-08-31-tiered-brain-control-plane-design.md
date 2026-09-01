# Tiered Brain Control Plane Design

Date: 2026-08-31
Status: Approved for implementation
Safety: PAPER/advisory only; no live-money execution

## Goal

Separate market-data ingestion, deterministic execution, scheduled deep analysis, and event-driven replanning so each layer uses the cheapest/fastest resource that fits its job. Strategy research can then evolve independently from the control plane.

## Architecture

### Azure deterministic runtime

Azure remains authoritative for market state and execution. It continuously stores 1-minute TradingView bars, derives canonical 5-minute bars/features, resolves the effective plan, evaluates stops/targets/risk, and runs the paper executor. GPT is never on the seconds-critical execution path.

At each completed 5-minute bar Azure evaluates the current Market Thesis anomaly policy. Extreme conditions may also be evaluated intra-5-minute. Azure never invents the anomaly definition: it executes structured conditions authored by the most recent Deep Brain thesis.

### Scheduled Deep Brain

The four staggered workers (:00/:15/:30/:45 America/Los_Angeles cadence inherited from Agent Hub) together provide one deep-analysis opportunity every 15 minutes. A Deep Brain run reads the rolling market-context snapshot, recent immutable theses/plans/analyses, current paper position/risk state, and the price-action strategy guide.

It writes:

1. an immutable `market_thesis_v1` document;
2. an immutable baseline `trading_plan_v1` document;
3. a compact structured analysis record;
4. scheduler heartbeat/current status so Azure can distinguish an expected run from an actually running/completed run.

The thesis defines expected regime, important levels, setup candidates, invalidation rules, and event-trigger/watch rules for the next analysis window.

### Event Brain

Azure creates one immutable `events/<event_id>.json` commit on the long-lived `event-stream` PR in `doomit/trading-brain-trigger` only when scheduler-aware gating says an immediate re-evaluation is worth the Event Task cost.

The event payload includes the triggered anomaly, baseline thesis/plan identifiers, market-context version, scheduler ETA/heartbeat, event budget, and requested analysis tier. Event Brain produces an immutable override/replan. It does not compete with the scheduler through last-writer-wins semantics.

Only one Event Brain may be inflight. Additional deviations are coalesced into durable pending market state rather than launching parallel Event Brain runs.

### Plan resolver

Azure is the only plan resolver. Baseline and override plans are immutable inputs. Resolver precedence is deterministic:

- an applicable, unexpired override whose `baseline_thesis_id` matches the active thesis supersedes its baseline;
- otherwise the newest applicable unexpired baseline is effective;
- stale state/thesis/symbol mismatches are rejected;
- high-severity `REPLAN_PENDING` freezes new entries while existing positions continue deterministic stop/target/risk management.

## Brain tiers

`L0` is deterministic/no GPT dispatch. `L1` is a focused event re-evaluation using the triggering condition, active thesis/plan, latest context and minimal recent evidence. `L2` is a normal event replan with a wider recent window and setup comparison. `L3` is an exceptional deep re-evaluation; it may inspect more stored strategy evidence but remains bounded to supplied trading context and does not perform arbitrary web research in execution flow.

Expected usage target is 0-6 Event Brain runs/hour, soft cap 8, hard cap 10. Emergency dispatch may exceed the soft cap but should stay below 12/hour. Deep scheduled analysis is preferred when the next healthy scheduler run is close.

## Scheduler-aware gating

The dispatcher considers:

- anomaly severity and requested tier;
- whether the observation is a completed 5-minute close or an emergency intra-bar event;
- time until next scheduled Deep Brain opportunity;
- actual scheduler heartbeat (`IDLE`, `RUNNING`, `COMPLETE`, `LATE`);
- whether an Event Brain is already inflight;
- cooldown/dedupe state;
- hourly Event Brain budget.

Default behavior:

- normal/no deviation: no event;
- low/medium deviation with next healthy Deep Brain <= 2 minutes: wait;
- medium deviation with scheduler 2-5 minutes away: normally wait, but dispatch if the scheduler is late or the thesis explicitly requests immediate re-evaluation;
- material invalidation with next Deep Brain > 5 minutes: dispatch L1/L2;
- critical invalidation/emergency: freeze new entries and dispatch L2/L3 without waiting;
- inflight event: coalesce instead of dispatching another event.

## Structured anomaly conditions

Do not execute arbitrary expression strings. Conditions are structured data with a known metric, comparator, numeric/metric threshold, timeframe/close-only semantics, severity, requested Brain tier, and optional cooldown. Supported comparator primitives begin with `GT`, `GTE`, `LT`, `LTE`, `CROSS_ABOVE`, and `CROSS_BELOW`.

Initial metrics are drawn from the existing causal feature set: OHLC, EMA20, ATR14, VWAP, OR5/15/30, current/previous session highs/lows, close-vs-EMA/VWAP normalized by ATR, body/tail/close-location, relative volume, and 1-minute microstructure aggregates.

## Initial price-action strategy guide

Both Deep and Event Brain use the same first-version strategy vocabulary:

- trend vs trading range / transition;
- EMA20 pullback/rejection and gap-to-EMA context;
- opening-range break/retest/failure;
- H1/H2 and L1/L2 pullback/re-entry structures;
- failed breakout / failed reversal / second-entry logic;
- inside-bar / ii compression and breakout quality;
- strong body / follow-through / exhaustion clues;
- measured move / AB=CD style objectives;
- session, prior-session, overnight, OR, VWAP and key swing levels;
- mandatory stop, explicit invalidation and intraday-only risk constraints.

Brain output records structured evidence and decision rationale, not hidden chain-of-thought.

## Data contracts and repository ownership

### `doomit/trading-core` public

Owns reusable schemas, validators, price-action context helpers, scheduler-aware dispatch logic, plan resolution, and tests.

### `doomit/trading-live` private

Owns thin Azure adapters: Azure Table state, GitHub App transport, rolling context publisher, timers, composition, deployment/config and diagnostics. No ordinary private Actions CI.

### `doomit/trading-runtime` private

Runtime/audit data only. On `gpt-runtime`:

- `runtime/market-context/current.json` — rolling bounded market snapshot (mutable pointer/snapshot);
- `runtime/brain-status/deep-current.json` — mutable scheduler heartbeat/status;
- `runtime/theses/<thesis_id>.json` — immutable Deep/Event thesis artifacts;
- `runtime/analyses/<analysis_id>.json` — immutable structured rationale/evidence;
- `runtime/plans/<plan_id>.json` — immutable trading plans.

Historical raw market bars remain authoritative in Azure. GitHub gets a bounded recent context window, not the full database.

### `doomit/trading-brain-trigger` private

Append-only machine trigger bus. The long-lived PR receives exactly one new immutable `events/<event_id>.json` file per Event Brain wake-up. No workflows and no unrelated commits on `event-stream`.

## Market-context mirror

Azure updates `runtime/market-context/current.json` after a newly completed 5-minute context is available. It contains a bounded recent 1m/5m window, current feature values, active thesis/plan IDs, position/risk snapshot, scheduler status and version/watermark. Updating this file does not wake Event Brain because the Event Task is scoped to the separate trigger repository.

## Execution safety

All new paths are PAPER-only. Real broker/live-money execution remains disabled. The rollout sequence is code/tests -> Azure deploy with dispatch/execution gates disabled -> synthetic full E2E -> live-market PAPER observation/dispatch -> paper execution. Enabling real market data through the paper decision path is not permission for live-money execution.
