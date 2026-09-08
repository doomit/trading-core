from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache
from importlib.resources import files
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker

from .simple_paper_contracts import plan_fits_quantity_limit, position_ref


SYMBOLS = ("MES", "MNQ")
EXECUTION_RULES_PATH = "config/simple-paper/execution-rules-v1.json"
STRATEGY_PATH = "docs/strategy/simple-paper-v1.md"


def market_path(symbol: str) -> str:
    return f"runtime/simple-paper/market/{_symbol(symbol)}/current.json"


def position_path(symbol: str) -> str:
    return f"runtime/simple-paper/position/{_symbol(symbol)}/current.json"


def plan_path(symbol: str) -> str:
    return f"runtime/simple-paper/plan/{_symbol(symbol)}/current.json"


def brain_run_path(run_id: str) -> str:
    value = str(run_id).strip()
    if not value:
        raise ValueError("run_id must not be empty")
    if "/" in value or "\\" in value or value in {".", ".."}:
        raise ValueError("run_id must be a single path-safe component")
    return f"runtime/simple-paper/brain-runs/{value}.json"


def run_scheduled_brain_job(
    *,
    run_id: str,
    scheduled_run_time: datetime,
    generated_at: datetime,
    read_runtime_json: Callable[[str], dict[str, Any] | None],
    read_control_json: Callable[[str], dict[str, Any]],
    read_control_text: Callable[[str], str],
    generate_plan: Callable[..., dict[str, Any]],
    publish_runtime_json: Callable[[str, dict[str, Any]], None],
    publish_run_evidence: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Run the stable Simple Paper Brain integration contract.

    Trading analysis itself is supplied by ``generate_plan``. This function owns
    deterministic contract I/O, validation, failure isolation, safe plan
    publication, and immutable run-evidence publication. One symbol may fail
    without replacing the other symbol's last valid plan.
    """
    _aware(scheduled_run_time, "scheduled_run_time")
    _aware(generated_at, "generated_at")

    result: dict[str, Any] = {
        "schema": "simple_paper_brain_run_v1",
        "run_id": str(run_id),
        "scheduled_run_time": _iso(scheduled_run_time),
        "generated_at": _iso(generated_at),
        "mode": "PAPER",
        "symbols": {},
        "result": "blocked_or_failed",
        "evidence_published": False,
        "evidence_error": None,
    }

    try:
        execution_rules = read_control_json(EXECUTION_RULES_PATH)
        _validate_schema("execution_rules_v1", execution_rules)
        strategy_prompt = read_control_text(STRATEGY_PATH)
        if not isinstance(strategy_prompt, str) or not strategy_prompt.strip():
            raise ValueError("strategy prompt is missing or empty")
    except Exception as exc:
        blocker = f"shared Brain input invalid: {exc}"
        for symbol in SYMBOLS:
            result["symbols"][symbol] = _empty_symbol_result(blocker)
        return _finalize_evidence(result, run_id, publish_run_evidence)

    published_count = 0
    for symbol in SYMBOLS:
        symbol_result = _empty_symbol_result(None)
        result["symbols"][symbol] = symbol_result

        try:
            market = read_runtime_json(market_path(symbol))
            if market is None:
                raise ValueError(f"market snapshot exact path {market_path(symbol)} is missing")
            _validate_schema("market_snapshot_v1", market)
            if market.get("symbol") != symbol:
                raise ValueError("market snapshot symbol mismatch")
            symbol_result["market_snapshot"] = {
                "updated_at": market.get("updated_at"),
                "latest_bar_end": market.get("latest_bar_end"),
            }
            symbol_result["analysis_bar_end"] = market.get("latest_bar_end")

            position = read_runtime_json(position_path(symbol))
            if position is None:
                raise ValueError(f"position exact path {position_path(symbol)} is missing")
            _validate_schema("position_state_v1", position)
            if position.get("symbol") != symbol:
                raise ValueError("position symbol mismatch")
            symbol_result["position"] = {
                "updated_at": position.get("updated_at"),
                "status": position.get("status"),
                "position_id": position.get("position_id"),
                "position_version": position.get("position_version"),
            }

            previous_plan = read_runtime_json(plan_path(symbol))
            if previous_plan is not None:
                try:
                    _validate_schema("trading_plan_v2", previous_plan)
                    if previous_plan.get("symbol") != symbol:
                        raise ValueError("previous plan symbol mismatch")
                except Exception as exc:
                    raise ValueError(f"previous plan is invalid: {exc}") from exc
                symbol_result["previous_plan"] = {
                    "plan_id": previous_plan.get("plan_id"),
                    "generated_at": previous_plan.get("generated_at"),
                }

            plan = generate_plan(
                symbol=symbol,
                market=market,
                position=position,
                execution_rules=execution_rules,
                previous_plan=previous_plan,
                strategy_prompt=strategy_prompt,
                generated_at=generated_at,
            )
            _validate_generated_plan(
                plan=plan,
                symbol=symbol,
                market=market,
                position=position,
                execution_rules=execution_rules,
            )
            symbol_result["output_validation"] = "passed"

            try:
                publish_runtime_json(plan_path(symbol), plan)
            except Exception as exc:
                symbol_result["blocker"] = f"plan write failed: {exc}"
                continue

            symbol_result["published_plan"] = {
                "plan_id": plan["plan_id"],
                "generated_at": plan["generated_at"],
                "analysis_bar_end": plan["analysis_bar_end"],
            }
            published_count += 1
        except Exception as exc:
            text = str(exc)
            if _looks_like_generated_plan_failure(text):
                symbol_result["output_validation"] = "failed"
            symbol_result["blocker"] = text

    if published_count == len(SYMBOLS):
        result["result"] = "published"
    elif published_count:
        result["result"] = "partial"
    return _finalize_evidence(result, run_id, publish_run_evidence)


def _validate_generated_plan(
    *,
    plan: dict[str, Any],
    symbol: str,
    market: dict[str, Any],
    position: dict[str, Any],
    execution_rules: dict[str, Any],
) -> None:
    try:
        _validate_schema("trading_plan_v2", plan)
    except Exception as exc:
        raise ValueError(f"generated plan schema validation failed: {exc}") from exc

    if plan.get("symbol") != symbol:
        raise ValueError("generated plan symbol mismatch")
    if plan.get("target_position") != position_ref(position):
        raise ValueError("generated plan target_position does not match exact observed Position")
    if plan.get("analysis_bar_end") != market.get("latest_bar_end"):
        raise ValueError("generated plan analysis_bar_end must match latest market bar used")

    analysis_time = _parse_time(plan["analysis_bar_end"])
    generation_time = _parse_time(plan["generated_at"])
    expiry_time = _parse_time(plan["action_valid_until"])
    if generation_time < analysis_time:
        raise ValueError("generated plan generated_at precedes analysis_bar_end")
    if expiry_time < generation_time:
        raise ValueError("generated plan action_valid_until precedes generated_at")

    max_contracts = int(execution_rules["max_contracts_per_symbol"])
    if not plan_fits_quantity_limit(plan, position, max_contracts):
        raise ValueError("generated plan quantity exceeds execution rule limit")


def _finalize_evidence(
    result: dict[str, Any],
    run_id: str,
    publish_run_evidence: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    result["evidence_published"] = True
    result["evidence_error"] = None
    try:
        publish_run_evidence(brain_run_path(run_id), result)
    except Exception as exc:
        result["evidence_published"] = False
        result["evidence_error"] = str(exc)
    return result


def _empty_symbol_result(blocker: str | None) -> dict[str, Any]:
    return {
        "market_snapshot": None,
        "position": None,
        "previous_plan": None,
        "analysis_bar_end": None,
        "output_validation": "not_attempted",
        "published_plan": None,
        "blocker": blocker,
    }


def _looks_like_generated_plan_failure(message: str) -> bool:
    lowered = message.lower()
    return "generated plan" in lowered or "target_position" in lowered or "quantity" in lowered


@lru_cache(maxsize=None)
def _schema(schema_name: str) -> dict[str, Any]:
    path = files("trading_core.schemas").joinpath(f"{schema_name}.schema.json")
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(schema_name: str, document: Any) -> None:
    if not isinstance(document, dict):
        raise ValueError(f"{schema_name} document must be an object")
    validator = Draft202012Validator(_schema(schema_name), format_checker=FormatChecker())
    errors = sorted(validator.iter_errors(document), key=lambda error: list(error.path))
    if errors:
        raise ValueError(errors[0].message)


def _parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    _aware(parsed, "timestamp")
    return parsed


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None:
        raise ValueError(f"{name} must include timezone")


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _symbol(symbol: str) -> str:
    value = str(symbol).strip().upper()
    if value not in SYMBOLS:
        raise ValueError(f"unsupported Simple Paper symbol: {symbol!r}")
    return value


__all__ = [
    "EXECUTION_RULES_PATH",
    "STRATEGY_PATH",
    "brain_run_path",
    "market_path",
    "plan_path",
    "position_path",
    "run_scheduled_brain_job",
]
