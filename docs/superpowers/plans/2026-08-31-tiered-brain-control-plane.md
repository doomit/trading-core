# Tiered Brain Control Plane Implementation Plan

> Scope: PAPER-only. No live-money execution. Private GitHub Actions are not used for ordinary validation.

## 1. Public control-plane contracts (`trading-core`)

Create failing tests first for:

- `market_thesis_v1` validation and normalized anomaly conditions;
- tier enum `L0..L3` and event-budget validation;
- scheduler-aware dispatch decisions including next-run ETA, late heartbeat, single-inflight coalescing, soft/hard caps, 5m-close preference and emergency bypass;
- baseline/override resolver precedence, thesis/state matching, expiration and `REPLAN_PENDING` new-entry freeze;
- initial deterministic price-action context tags built only from supplied current/prior causal features.

Then implement the minimal reusable modules and exports. Keep existing `trading_plan_v1` compatibility while adding optional source/role/thesis/state metadata. Standardize production `analysis_summary` on a string array.

## 2. Shared price-action guide

Add `docs/strategy/price-action-v1.md` describing the setup vocabulary both Brain paths must use: trend/range, EMA20 tests, OR break/retest/failure, H1/H2/L1/L2, failed breakout/re-entry, inside/ii compression, strong body/follow-through, measured moves, key session levels, and mandatory risk/invalidation. The guide defines evidence fields, not a hard-coded profitable-strategy claim.

## 3. Azure commit-trigger transport (`trading-live`)

Replace the obsolete bot-comment wake-up adapter with a GitHub App content publisher that creates exactly one immutable `events/<event_id>.json` on `doomit/trading-brain-trigger:event-stream`. Generalize durable event state from comment-specific naming to dispatch semantics while keeping old rows readable. Add thin private tests; do not create/trigger private Actions.

## 4. Azure market context + scheduler-aware dispatcher

Add thin adapters/timer orchestration that:

- publishes bounded `runtime/market-context/current.json` to `doomit/trading-runtime:gpt-runtime` when a complete 5m context advances;
- reads `runtime/brain-status/deep-current.json` for real scheduler heartbeat;
- evaluates the active thesis's structured anomaly rules with public-core logic;
- computes next scheduled Deep Brain ETA;
- enforces one Event Brain inflight, dedupe/cooldown and hourly budget;
- writes a durable event record before publishing the trigger commit;
- enters `REPLAN_PENDING`/freeze-new-entry on high-severity invalidation;
- leaves existing position stop/target/risk management deterministic.

The future Azure-side LLM/basic-analysis seam remains a placeholder; no Azure LLM call is added now.

## 5. Plan resolution and paper execution integration

Wire public-core resolver decisions into the thin Azure execution path so only the resolved effective plan can reach `RiskGateway`/`PaperBroker`. Preserve exact-once reservation and immutable execution-input freezing. Continue to fail closed on stale plan/thesis/state binding.

## 6. Deep Scheduler Brain (`agent-operating-hub` + Tasks)

Change the four staggered workers so each wake first performs one bounded `trading_deep_brain` pass when the market context has advanced and that 15-minute slot is not already covered. The pass:

1. writes `RUNNING` heartbeat;
2. reads bounded current context + recent theses/plans/analyses;
3. applies `price-action-v1`;
4. writes immutable thesis, structured analysis and baseline plan;
5. writes `COMPLETE` heartbeat with output IDs;
6. returns to the normal global work queue for remaining run time.

Disable the old comment-based `trading_brain_scheduler_fallback` after the new Event path is wired, so scheduler and Event Brain cannot manufacture duplicate plans for the same anomaly.

## 7. Production Event Brain Task

Update existing webhook task `Brain Commit Trigger` without changing its registered GitHub trigger. It validates the new event schema/tier, reads only the referenced bounded context and active baseline artifacts, applies `price-action-v1`, and writes an immutable override plan plus structured analysis. L1 is narrow, L2 wider, L3 deepest but bounded; no arbitrary web research in execution flow.

## 8. Verification before Azure mutation

Verify:

- public-core targeted tests and full suite where available;
- private branch diffs contain no automatic Actions triggers;
- GitHub App transport targets only the trigger repo/event-stream;
- Task prompt is production, webhook-driven and enabled;
- scheduler workers are enabled and use one shared deep-analysis contract;
- all execution flags remain PAPER-only.

## 9. One-shot Azure rollout script

Generate one Cloud Shell script that performs preflight before mutation, deploys the exact prepared Function package directly (no GitHub Actions), applies new app settings with event dispatch and real-market paper execution initially disabled, verifies functions/settings, then runs a synthetic complete E2E:

Azure event -> GitHub App bot event-file commit -> ChatGPT Event Brain -> immutable override plan -> Azure pickup/resolver -> RiskGateway -> PaperBroker -> durable receipt/audit.

Only after synthetic E2E PASS should the script (or a clearly separated final command) enable live-market PAPER dispatch/plan evaluation. Real-money broker execution remains disabled.
