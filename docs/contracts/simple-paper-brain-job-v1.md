# Simple Paper Brain Job v1

## Purpose

Define the stable contract for the scheduled ChatGPT Brain job used by Simple Paper Runtime v1. This contract describes orchestration and I/O only. Trading-analysis strategy lives in a separate strategy prompt so strategy can change without rewriting the job.

## Schedule and priority

- Run every 15 minutes on quarter-hour boundaries.
- Hub priority: `urgent`.
- Existing overdue work may run ahead of it; otherwise it outranks normal work.
- Do not create or use Event Brain in v1.
- PAPER only.

## Inputs

For each symbol `MES` and `MNQ`, read the latest GitHub-owned values below.

1. **Market snapshot** — must validate as `market_snapshot_v1`.
2. **Current position** — must validate as `position_state_v1`.
3. **Execution rules** — must validate as `execution_rules_v1`.
4. **Previous/latest plan** — if present, must validate as `trading_plan_v2` before it is used as context.
5. **Strategy prompt** — a separately versioned Markdown document describing how to analyze price action and choose a plan.

Every JSON input must contain its own timestamp(s). The job must report the exact latest one-minute `analysis_bar_end` it used.

The job may use retained conversation context or previously observed bars as supplemental analysis context, but correctness must not depend on hidden memory. If retained context conflicts with the current GitHub contracts, the current GitHub contracts win.

## Market analysis

- The job consumes one-minute OHLCV bars directly.
- It may derive 5-minute bars, indicators, price-action structures, and other analysis locally.
- It must not require Azure-produced market-context, Canonical5m, deep-input, BAR_READY, or dashboard state.
- If the latest market snapshot is malformed or materially stale, emit no actionable OPEN/ADD/REDUCE instruction. Record the blocker in analysis summary and prefer `NO_TRADE` for FLAT or `HOLD` for OPEN when the contract can be satisfied safely.

## Position binding

The output plan must bind to exactly the position state that the job observed:

- If current position status is `FLAT`, output `target_position = {state: FLAT, position_id: null, position_version: null}`.
- If current position status is `OPEN`, output `target_position = {state: OPEN, position_id: <exact id>, position_version: <exact integer version>}`.

Never guess or synthesize a position id/version.

## Plan output

Write exactly one latest plan for MES and one latest plan for MNQ. Each output must validate as `trading_plan_v2` before publication.

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

1. Read and validate the five input classes above.
2. Analyze MES and MNQ according to the current strategy prompt.
3. Generate one contract-valid `trading_plan_v2` per symbol bound to the exact observed position.
4. Validate output before write.
5. Write a concise Brain run log with input timestamps, output timestamps, and blockers.

The stable job prompt must not embed detailed strategy rules. Strategy rules belong only in the strategy prompt document so a strategy change does not require changing the scheduler task.

## Failure behavior

- Failure reading one symbol must not corrupt or overwrite the other symbol's last valid plan.
- Invalid input: do not publish a new plan for that symbol; log the validation failure.
- Invalid generated plan: do not publish it; log the schema/semantic validation failure.
- GitHub write failure: do not pretend the plan was published.
- A failed scheduled run never authorizes live trading and never changes Azure Position directly.

## Observability

For every symbol attempted, the Brain run log should expose at least:

- scheduled run time;
- market snapshot `updated_at` and `latest_bar_end`;
- position `updated_at`, `position_id`, and `position_version`;
- previous plan id/generated time, if present;
- new plan id/generated time, if successfully published;
- `analysis_bar_end`;
- input age / analysis latency that can be derived from those timestamps;
- output validation result;
- blocker/error, if any.

The execution loop independently records plan pickup time so end-to-end Brain latency and pickup latency can be calculated without inferring from dashboard state.
