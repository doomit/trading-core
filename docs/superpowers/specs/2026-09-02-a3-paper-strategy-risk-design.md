# Aggressive A3 PAPER Strategy and Risk Design

Date: 2026-09-02
Status: approved by user directive
Scope: PAPER only. Live-money execution remains disabled.

## Goal

Remove false execution blockers first, then introduce a new immutable `PA_AGGRESSIVE_A3_TREND` PAPER profile that is materially more willing than A2 to follow strong trends. Risk is controlled through deterministic provenance/freshness gates, mandatory structural stops, runtime-configured limits, safe quantity downsize, session/kill-switch controls, and replay/shadow evidence rather than broad `NO_TRADE` conservatism.

## Non-negotiable development process

Every production behavior change uses strict TDD:

1. Write an acceptance/regression test first.
2. Run it before implementation and observe RED for the intended missing behavior/bug.
3. Write the smallest production change that makes it GREEN.
4. Run the exact changed-component tests.
5. Run the P0 fast gate after every logic change.
6. Run the complete P1 public suite for major A3 profile versions, merge/release, and before Azure candidate activation.

Ordinary tests run in public `doomit/trading-core` or the worker execution environment. Private GitHub Actions are reserved for Azure-specific stage/switch/rollback or otherwise irreducible Azure integration verification.

### Test levels

- **P0 fast:** `tests/test_configurable_risk.py`, `tests/test_runtime_config.py`, `tests/test_paper_execution.py`, `tests/test_paper_execution_contract.py`, plus the exact changed component/regression test. Run after every logic change.
- **Component:** all tests directly coupled to the changed component and its error/edge contracts. Run after every sub-part.
- **P1 full:** `pytest -q` for major A3 version publication, merge/release, aggregate release candidate, and before Azure activation.

## P0.1 — Feed trust and freshness semantics

### Current defect

`ConfigurableRiskGateway` currently returns `UNTRUSTED_FEED` when any of provenance (`environment`, `data_class`, `source`) or `market.healthy` fails, and separately rejects feed age above the compiled 90-second constant as `STALE_FEED`. The private execution snapshot builder currently derives `market.healthy` from the same `<=90s` age check. This duplicates freshness inside trust and caused an actionable PAPER plan to be classified `UNTRUSTED_FEED` instead of being evaluated by the explicit stale gate.

### Required semantics

- Trust/quality and freshness are separate.
- `UNTRUSTED_FEED` covers provenance/structural data-quality failure: not PROD, not REAL, not tradingview, or explicit structural unhealthy/untrusted state.
- Feed age is evaluated independently as `STALE_FEED`.
- The maximum PAPER feed age becomes a validated runtime-config field.
- A2 compatibility default remains 90 seconds when parsing legacy config documents that omit the field, so existing immutable A2 documents remain valid.
- A3 candidate explicitly sets `max_feed_age_seconds=180`.
- Negative feed age remains `FEED_TIME_IN_FUTURE`.
- Data past the configured threshold remains fail-closed.

### Public/private boundary

Public core owns the runtime-config field, validation, serialization and deterministic risk decision. Private `trading-live` owns conversion from Azure/raw bars into a `MarketSnapshot`; its adapter must stop using feed age to synthesize structural trust once the public contract is available. Private adapter changes are tested locally/worker-side first and activated only through versioned rollout.

## P0.2 — Event Brain capacity must be effective

Public schema already supports `account_capacity`; private code already projects runtime capacity. The remaining work is end-to-end verification and explicit failure observability. A capacity-positive PAPER event must not decline merely because open-micro capacity was omitted. Enrichment failure must be distinguishable from a strategy `NO_TRADE`.

## P0.3 — Runtime/Deep Brain liveness

Open-session feed, market-context, Deep Brain heartbeat and plan coverage must continue advancing. Stale/missed Deep Brain coverage is a liveness defect. Workers honor current global leases and do not interfere with active live-ingress recovery.

## P0 dependency — Versioned Azure rollout

No code is deployed over the active trading runtime in place. Every activation uses immutable candidate identity, isolated candidate target, PAPER fencing, exact health/config verification, explicit switch, post-switch verification and retained rollback target.

## P1 — `PA_AGGRESSIVE_A3_TREND`

A2 remains immutable control/rollback. A3 adds explicit momentum/trend-chase permission.

A3 may propose next-bar continuation entries without mandatory pullback/retest when completed-bar evidence supports:

- strong directional 5m body and close location;
- EMA20/VWAP alignment or decisive reclaim/acceptance;
- breakout/acceptance through OR, session or meaningful swing structure;
- a valid structural stop/invalidation;
- usable reward geometry after accounting for nearby structure.

Extension from EMA/VWAP is not itself an automatic `NO_TRADE`. Extension can reduce quantity/risk budget, shorten plan validity or demand stronger evidence. Mixed mid-range chop, stale/untrusted feed, invalid stop geometry and genuinely poor structural reward remain valid rejection reasons.

No lookahead: analysis uses closed bars and entry remains no earlier than the next eligible executable bar.

## P1 — A3 risk candidate

Initial candidate values:

- `min_action_confidence = 0.55`
- `target_risk_per_trade_usd = 750`
- `max_risk_per_trade_usd = 1500`
- `max_open_micro_contracts = 20`
- `max_daily_realized_loss_usd = 7500`
- `max_consecutive_losses = 5`
- `max_entries_per_session = 40`
- `max_feed_age_seconds = 180`

Mandatory protective stop, take-profit contract, PAPER-only mode, kill switch, session limits, config/profile identity, plan validity and safe quantity downsize remain deterministic.

## A3 release acceptance

Before A3 activation:

- every behavior change has observed RED-before-GREEN evidence;
- P0 fast gate passes after each logic change;
- component tests pass after each sub-part;
- exact A3 release-candidate SHA passes full public `pytest -q`;
- A2 vs A3 replay/shadow scenarios record candidate, no-trade and rejection reasons, not only PnL;
- the prior false feed-trust case is no longer `UNTRUSTED_FEED`;
- capacity-positive Event Brain input contains capacity;
- isolated Azure candidate passes PAPER E2E;
- explicit version switch preserves rollback;
- `live_money=NOT_CONNECTED_NOT_AUTHORIZED` remains true.
