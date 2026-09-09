# Simple Paper Brain Job v1

## Purpose

Define the stable contract for the scheduled ChatGPT Brain job used by Simple Paper Runtime v1. This contract describes orchestration and I/O only. Trading-analysis strategy lives in a separate strategy prompt so strategy can change without rewriting the job.

## Schedule and priority

- Run every 15 minutes on quarter-hour boundaries.
- Hub priority: `urgent`.
- Existing overdue work may run ahead of it; otherwise it outranks normal work.
- Do not create or use Event Brain in v1.
- PAPER only.

## Canonical GitHub locations

The paths below are part of the v1 contract. Consumers must use exact path lookup; they must not scan historical files to discover “latest”.

Runtime data repository: `doomit/trading-runtime`, branch `gpt-runtime`.

For `SYMBOL` = `MES` or `MNQ`:

- Market snapshot: `runtime/simple-paper/market/SYMBOL/current.json`
- Current position: `runtime/simple-paper/position/SYMBOL/current.json`
- Latest Brain plan: `runtime/simple-paper/plan/SYMBOL/current.json`
- Immutable Brain run evidence: `runtime/simple-paper/brain-runs/<run_id>.json`

Stable public control repository: `doomit/trading-core`, branch `main` after this contract PR merges.

- Execution rules: `config/simple-paper/execution-rules-v1.json`
- Strategy prompt: `docs/strategy/simple-paper-v1.md`
- Schemas: `src/trading_core/schemas/*.schema.json`

Azure remains authoritative for market bars and Position. GitHub market and position files are best-effort mirrors for Brain. Market freshness is determined from the market snapshot timestamps. Position freshness must not be inferred from the age of `Position.updated_at`; the Position document carries state identity, not a heartbeat. Missing or malformed exact-path inputs remain blockers, while Azure's authoritative position binding protects execution from a stale-but-valid GitHub Position observation.

## Inputs

For each symbol `MES` and `MNQ`, read the exact GitHub locations above.

1. **Market snapshot** — must validate as `market_snapshot_v1`.
2. **Current position** — must validate as `position_state_v1`.
3. **Execution rules** — must validate as `execution_rules_v1`.
4. **Previous/latest plan** — if present, must validate as `trading_plan_v2` before it is used as context.
5. **Strategy prompt** — the separately versioned Markdown document describing how to analyze price action and choose a plan.

Every JSON input must contain its own timestamp(s). The job must report the exact latest one-minute `analysis_bar_end` it used.

The job may use retained conversation context or previously observed bars as supplemental analysis context, but correctness must not depend on hidden memory. If retained context conflicts with the current GitHub contracts, the current GitHub contracts win.

## Market analysis

- The job consumes one-minute OHLCV bars directly.
- It may derive 5-minute bars, indicators, price-action structures, and other analysis locally.
- It must not require Azure-produced market-context, Canonical5m, deep-input, BAR_READY, or dashboard state.
- If the latest market snapshot is malformed or materially stale, emit no actionable OPEN/ADD/REDUCE instruction. Record the blocker in analysis summary and prefer `NO_TRADE` for FLAT or `HOLD` for OPEN when the contract can be satisfied safely.

## Position binding

Position.updated_at is a state-change timestamp, not a liveness heartbeat. The Brain must not classify a valid Position mirror as stale solely because `updated_at` is old. Position liveness is not inferred from timestamp age; execution safety comes from binding every plan to the exact observed position state and from Azure re-validating that binding against its authoritative Position before any paper action.

The output plan must bind to exactly the position state that the job observed:

- If current position status is `FLAT`, output `target_position = {state: FLAT, position_id: null, position_version: null}`. Therefore a valid exact-path `FLAT` Position may be used to generate an `OPEN` candidate regardless of the age of `updated_at`. Azure must re-check the current authoritative Position before executing that candidate; if Azure is no longer FLAT, the candidate must not execute.
- If current position status is `OPEN`, output `target_position = {state: OPEN, position_id: <exact id>, position_version: <exact integer version>}`. For `OPEN`, bind to the exact observed `position_id` and `position_version`; `updated_at` age alone is not a blocker. Azure must reject actions whose position identity/version no longer matches authoritative state.

Never guess or synthesize a position id/version. A missing, unreadable, or schema-invalid Position file is a real blocker; an old `updated_at` value by itself is not.

## Plan output

Write exactly one latest plan to each exact `runtime/simple-paper/plan/SYMBOL/current.json` path. Each output must validate as `trading_plan_v2` before publication.

Required plan semantics:

- `analysis_bar_end` is the newest one-minute bar actually used.
- `generated_at` is the actual plan generation time.
- `action_valid_until` limits OPEN/ADD/REDUCE/EXIT actions; expired actions must not be revived.
- `decision` is one of `NO_TRADE`, `HOLD`, `OPEN`, `UPDATE`, `EXIT`.
- OPEN requires direction, entry instruction, stop-loss, and take-profit.
- Quantity is an integer and may not exceed the current `execution_rules_v1.max_contracts_per_symbol` (v1 maximum 6).
- At most one `add_once` and one `reduce_once` instruction may be emitted.
- Do not emit unsupported executable keywords.

The Brain proposes actions. Azure remains responsible for matching the candidate plan to current position state and for deterministic paper execution.

## Strategy separation

The stable scheduled-job prompt should say, in substance:

1. Read and validate the five input classes above from their exact paths.
2. Analyze MES and MNQ according to the current strategy prompt.
3. Generate one contract-valid `trading_plan_v2` per symbol bound to the exact observed position.
4. Validate output before write.
5. Write a concise immutable Brain run log with input timestamps, output timestamps, and blockers.

The stable job prompt must not embed detailed strategy rules. Strategy rules belong only in `docs/strategy/simple-paper-v1.md` so a strategy change does not require changing the scheduler task.

## Failure behavior

- Failure reading one symbol must not corrupt or overwrite the other symbol's last valid plan.
- Invalid input: do not publish a new plan for that symbol; log the validation failure.
- Invalid generated plan: do not publish it; log the schema/semantic validation failure.
- GitHub write failure: do not pretend the plan was published.
- A failed scheduled run never authorizes live trading and never changes Azure Position directly.

## Observability

For every symbol attempted, the Brain run evidence should expose at least:

- scheduled run time;
- market snapshot `updated_at` and `latest_bar_end`;
- position `updated_at`, `position_id`, and `position_version` (for audit only; do not treat `position.updated_at` age as a heartbeat freshness signal);
- previous plan id/generated time, if present;
- new plan id/generated time, if successfully published;
- `analysis_bar_end`;
- input age / analysis latency that can be derived from timestamps that actually represent freshness, such as market timestamps;
- output validation result;
- blocker/error, if any.

The execution loop independently records plan pickup time so end-to-end Brain latency and pickup latency can be calculated without inferring from dashboard state.
