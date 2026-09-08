from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

from .simple_paper_contracts import (
    ACCEPTED,
    candidate_plan_outcome,
    plan_latency_ms,
    position_ref,
)


PLAN_PATH_TEMPLATE = "runtime/simple-paper/plan/{symbol}/current.json"


def run_symbol_execution_cycle(
    *,
    symbol: str,
    cycle_id: str,
    tick_at: datetime,
    load_position: Callable[[str], dict[str, Any]],
    maybe_eod_close: Callable[[dict[str, Any], datetime], dict[str, Any] | None],
    read_plan: Callable[[str], dict[str, Any] | None],
    write_plan_observation: Callable[[dict[str, Any]], None],
    load_market: Callable[[str], dict[str, Any] | None],
    manage_open_position: Callable[
        [dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, datetime],
        dict[str, Any],
    ],
    execute_flat: Callable[
        [dict[str, Any], dict[str, Any] | None, dict[str, Any] | None, datetime],
        dict[str, Any],
    ],
    write_cycle_log: Callable[[dict[str, Any]], None],
    last_observed_plan_id: str | None = None,
) -> dict[str, Any]:
    """Run one short-lived, reentrant Simple Paper symbol execution cycle.

    The orchestration layer never persists position/account state itself. State-changing
    callbacks are action boundaries and must persist their own durable result before
    returning. Plan/market read failures are contained so an existing OPEN position is
    still given a management attempt from durable state.
    """
    if symbol not in {"MES", "MNQ"}:
        raise ValueError(f"unsupported symbol: {symbol!r}")
    if tick_at.tzinfo is None:
        raise ValueError("tick_at must include timezone")

    start_position = load_position(symbol)
    current_position = start_position

    eod_result = maybe_eod_close(current_position, tick_at)
    if eod_result is not None:
        result = _normalize_action_result(eod_result, current_position)
        cycle_log = _build_cycle_log(
            symbol=symbol,
            cycle_id=cycle_id,
            tick_at=tick_at,
            start_position=start_position,
            end_position=result["position"],
            market=None,
            plan_id=None,
            outcome=result["outcome"],
            synthetic=result["synthetic"],
            eod_forced=result["outcome"] == "EOD_FORCED_CLOSE",
            stale_feed=result["stale_feed"],
            error=result.get("error"),
        )
        _best_effort_cycle_log(write_cycle_log, cycle_log)
        return cycle_log

    accepted_plan: dict[str, Any] | None = None
    candidate_plan: dict[str, Any] | None = None
    plan_error: str | None = None
    plan_outcome = "NO_NEW_PLAN"
    generation_latency: int | None = None
    pickup_latency: int | None = None

    try:
        raw_candidate_plan = read_plan(PLAN_PATH_TEMPLATE.format(symbol=symbol))
    except Exception as exc:  # adapter failure must not block durable OPEN management
        plan_outcome = "READ_FAILED"
        plan_error = str(exc)
    else:
        if raw_candidate_plan is not None and not isinstance(raw_candidate_plan, dict):
            plan_outcome = "INVALID"
            plan_error = "candidate plan must be a JSON object"
        else:
            candidate_plan = raw_candidate_plan
            if candidate_plan is not None:
                try:
                    plan_outcome = candidate_plan_outcome(
                        candidate_plan,
                        current_position,
                        tick_at,
                        last_observed_plan_id=last_observed_plan_id,
                    )
                    generation_latency, pickup_latency = plan_latency_ms(
                        candidate_plan["analysis_bar_end"],
                        candidate_plan["generated_at"],
                        tick_at,
                    )
                    if plan_outcome == ACCEPTED:
                        accepted_plan = candidate_plan
                except Exception as exc:
                    plan_outcome = "INVALID"
                    plan_error = str(exc)
                    accepted_plan = None

    observation = _build_plan_observation(
        symbol=symbol,
        cycle_id=cycle_id,
        tick_at=tick_at,
        position=current_position,
        candidate_plan=candidate_plan,
        outcome=plan_outcome,
        generation_latency=generation_latency,
        pickup_latency=pickup_latency,
        error=plan_error,
    )
    try:
        write_plan_observation(observation)
    except Exception as exc:
        plan_error = _join_error(plan_error, f"plan_observation_log: {exc}")

    market: dict[str, Any] | None = None
    market_error: str | None = None
    try:
        market = load_market(symbol)
    except Exception as exc:
        market_error = str(exc)

    try:
        if current_position["status"] == "OPEN":
            action_result = manage_open_position(current_position, accepted_plan, market, tick_at)
        elif current_position["status"] == "FLAT":
            if accepted_plan is None:
                action_result = {
                    "position": current_position,
                    "outcome": "NO_ACTION",
                    "synthetic": False,
                    "stale_feed": False,
                }
            else:
                action_result = execute_flat(current_position, accepted_plan, market, tick_at)
        else:
            raise ValueError("current position must be FLAT or OPEN")
        result = _normalize_action_result(action_result, current_position)
        action_error = result.get("error")
    except Exception as exc:
        result = {
            "position": current_position,
            "outcome": "ERROR",
            "synthetic": False,
            "stale_feed": False,
        }
        action_error = str(exc)

    cycle_log = _build_cycle_log(
        symbol=symbol,
        cycle_id=cycle_id,
        tick_at=tick_at,
        start_position=start_position,
        end_position=result["position"],
        market=market,
        plan_id=candidate_plan.get("plan_id") if candidate_plan else None,
        outcome=result["outcome"],
        synthetic=result["synthetic"],
        eod_forced=False,
        stale_feed=result["stale_feed"],
        error=_join_error(plan_error, market_error, action_error),
    )
    _best_effort_cycle_log(write_cycle_log, cycle_log)
    return cycle_log


def _build_plan_observation(
    *,
    symbol: str,
    cycle_id: str,
    tick_at: datetime,
    position: dict[str, Any],
    candidate_plan: dict[str, Any] | None,
    outcome: str,
    generation_latency: int | None,
    pickup_latency: int | None,
    error: str | None,
) -> dict[str, Any]:
    return {
        "schema": "plan_observation_log_v1",
        "log_id": f"{cycle_id}:{symbol}:plan",
        "symbol": symbol,
        "pulled_at": _iso_z(tick_at),
        "candidate_plan_id": candidate_plan.get("plan_id") if candidate_plan else None,
        "candidate_generated_at": candidate_plan.get("generated_at") if candidate_plan else None,
        "analysis_bar_end": candidate_plan.get("analysis_bar_end") if candidate_plan else None,
        "current_position": position_ref(position),
        "outcome": outcome,
        "brain_generation_latency_ms": generation_latency,
        "plan_pickup_latency_ms": pickup_latency,
        "error": error,
    }


def _build_cycle_log(
    *,
    symbol: str,
    cycle_id: str,
    tick_at: datetime,
    start_position: dict[str, Any],
    end_position: dict[str, Any],
    market: dict[str, Any] | None,
    plan_id: str | None,
    outcome: str,
    synthetic: bool,
    eod_forced: bool,
    stale_feed: bool,
    error: str | None,
) -> dict[str, Any]:
    latest_market_end = market.get("latest_bar_end") if market else None
    market_age = _market_age_ms(latest_market_end, tick_at)
    return {
        "schema": "execution_cycle_log_v1",
        "log_id": f"{cycle_id}:{symbol}",
        "cycle_id": cycle_id,
        "tick_at": _iso_z(tick_at),
        "symbol": symbol,
        "latest_market_end": latest_market_end,
        "market_age_ms": market_age,
        "start_position": position_ref(start_position),
        "end_position": position_ref(end_position),
        "plan_id": plan_id,
        "outcome": outcome,
        "synthetic": bool(synthetic),
        "eod_forced": bool(eod_forced),
        "stale_feed": bool(stale_feed),
        "error": error,
    }


def _normalize_action_result(result: dict[str, Any], fallback_position: dict[str, Any]) -> dict[str, Any]:
    return {
        "position": result.get("position", fallback_position),
        "outcome": result.get("outcome", "NO_ACTION"),
        "synthetic": bool(result.get("synthetic", False)),
        "stale_feed": bool(result.get("stale_feed", False)),
        "error": result.get("error"),
    }


def _market_age_ms(latest_market_end: str | None, tick_at: datetime) -> int | None:
    if latest_market_end is None:
        return None
    end = datetime.fromisoformat(latest_market_end.replace("Z", "+00:00"))
    if end.tzinfo is None:
        raise ValueError("latest_market_end must include timezone")
    age_ms = int((tick_at - end).total_seconds() * 1000)
    return max(0, age_ms)


def _best_effort_cycle_log(write_cycle_log: Callable[[dict[str, Any]], None], log: dict[str, Any]) -> None:
    # Never retry an already-persisted action merely because summary logging failed.
    try:
        write_cycle_log(log)
    except Exception:
        return


def _join_error(*parts: str | None) -> str | None:
    values = [part for part in parts if part]
    return "; ".join(values) if values else None


def _iso_z(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")
