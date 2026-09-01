# Price Action Strategy Guide v1

Purpose: shared PAPER-trading analysis vocabulary for Scheduled Deep Brain and Event Brain. This is a research baseline, not a claim of proven edge. Every proposed trade must still pass deterministic risk and execution gates.

## Analysis order

1. **Context / regime** — trend, trading range, transition, volatility, session phase.
2. **Location** — EMA20, VWAP, opening range, current/prior session high/low/close, overnight high/low, recent swing points and round-number context when supplied.
3. **Signal quality** — bar body/tails/close location, follow-through, overlap, micro path efficiency, relative volume.
4. **Setup** — identify the smallest supported setup family below.
5. **Invalidation first** — state the price/structure that makes the thesis wrong.
6. **Trade plan** — LONG/SHORT/NO_TRADE/HOLD/EXIT/UPDATE, entry condition, mandatory stop, targets/management, confidence.
7. **Watch conditions** — structured anomaly rules Azure can evaluate without an LLM.

## Setup families

### Trend pullback / EMA20

Prefer a clear directional structure with price holding the trend side of EMA20/VWAP. Look for pullback tests/rejections rather than chasing extended bars. Treat a decisive close through the defended EMA/structure as potential thesis invalidation.

### H1/H2 and L1/L2

Use first/second-entry pullback logic only when the surrounding trend/range context supports it. A second entry is not independently bullish/bearish; location, bar quality, failed attempts and follow-through matter.

### Opening range

Use OR5/OR15/OR30 as location and structure. Distinguish:

- breakout with acceptance/follow-through;
- breakout then retest/hold;
- failed breakout back into the range;
- repeated tests that weaken a boundary.

### Failed breakout / failed reversal / re-entry

A failed attempt can be more informative than an initial breakout. Prefer explicit failure evidence (close back through level, strong opposite bar, second-entry confirmation) and a tight structural invalidation.

### Inside bar / ii / compression

Compression is a setup precursor, not a direction by itself. Direction comes from context, location and breakout/failure quality. Avoid treating low-volatility noise as a high-confidence signal.

### Strong body / follow-through / exhaustion

Large body, strong close location and directional micro path can support momentum. Do not extrapolate indefinitely: extension from EMA/VWAP, repeated climaxes and poor follow-through reduce continuation quality.

### Measured move / AB=CD

Use measured moves as candidate objectives/context rather than guaranteed targets. Confirm that the projected move is compatible with nearby session/OR/VWAP/swing structure and current volatility.

## Required structured output

Every Brain analysis should record compact evidence rather than hidden chain-of-thought:

- `regime`
- `setup_candidates`
- `supporting_evidence`
- `contrary_evidence`
- `key_levels`
- `invalidation`
- `watch_conditions`
- `decision`
- `confidence`
- `analysis_summary` as a short array of strings

## Event Brain behavior

Event Brain starts from the active baseline thesis. It answers: what changed, which watch/invalidation rule fired, whether the baseline thesis still holds, and whether the effective plan must be canceled/updated/replaced. It should not restart broad market research for an L1/L2 event.

## Scheduled Deep Brain behavior

Deep Brain may inspect a wider recent market window plus recent theses/plans/structured analyses. It should actively compare candidate setups, review whether prior invalidation/watch conditions were useful, and publish the next baseline thesis and anomaly policy.

## Safety

- PAPER/advisory only.
- Mandatory stop for any actionable entry.
- Intraday position-management constraints remain deterministic.
- When evidence is mixed or data quality is insufficient, prefer `NO_TRADE`.
