from __future__ import annotations

from copy import deepcopy
from datetime import datetime
from typing import Any, Callable

from .simple_paper_contracts import (
    position_ref,
    validate_paper_account_state_semantics,
    validate_paper_execution_semantics,
)


def fill_price_for_order(order: dict[str, Any], side: str, bar: dict[str, float]) -> float | None:
    """Resolve one explicit MARKET/LIMIT/STOP paper instruction against one bar."""
    order_type = order["order_type"]
    if order_type == "MARKET":
        return float(bar["close"])

    trigger = float(order["trigger_price"])
    if order_type == "LIMIT":
        touched = float(bar["low"]) <= trigger if side == "LONG" else float(bar["high"]) >= trigger
    elif order_type == "STOP":
        touched = float(bar["high"]) >= trigger if side == "LONG" else float(bar["low"]) <= trigger
    else:
        raise ValueError(f"unsupported order_type: {order_type!r}")
    return trigger if touched else None


def new_flat_position(symbol: str, updated_at: datetime) -> dict[str, Any]:
    """Build the canonical durable FLAT current-position state."""
    if symbol not in {"MES", "MNQ"}:
        raise ValueError(f"unsupported symbol: {symbol!r}")
    return {
        "schema": "position_state_v1",
        "symbol": symbol,
        "status": "FLAT",
        "position_id": None,
        "position_version": None,
        "side": None,
        "qty": 0,
        "avg_entry": None,
        "stop_loss": None,
        "take_profit": None,
        "active_plan_id": None,
        "active_plan_generated_at": None,
        "action_valid_until": None,
        "pending_add": None,
        "pending_reduce": None,
        "opened_at": None,
        "updated_at": _iso_z(updated_at),
        "closed_at": None,
        "last_action_id": None,
    }


def open_position_from_plan(
    *,
    current_position: dict[str, Any],
    plan: dict[str, Any],
    fill_price: float,
    executed_at: datetime,
    position_id: str,
) -> dict[str, Any]:
    """Create a new durable OPEN position from a matching OPEN plan."""
    if current_position.get("status") != "FLAT":
        raise ValueError("new position may open only from FLAT")
    if plan.get("decision") != "OPEN":
        raise ValueError("plan decision must be OPEN")
    if plan.get("symbol") != current_position.get("symbol"):
        raise ValueError("plan symbol must match current position symbol")

    entry = plan["entry"]
    qty = int(entry["qty"])
    add_qty = int((plan.get("add_once") or {}).get("qty", 0))
    if qty + add_qty > 6:
        raise ValueError("OPEN plus add_once may not exceed maximum 6 contracts")

    protection = plan["protection"]
    executed_at_iso = _iso_z(executed_at)

    return {
        "schema": "position_state_v1",
        "symbol": current_position["symbol"],
        "status": "OPEN",
        "position_id": position_id,
        "position_version": 0,
        "side": plan["side"],
        "qty": qty,
        "avg_entry": float(fill_price),
        "stop_loss": float(protection["stop_loss"]),
        "take_profit": float(protection["take_profit"]),
        "active_plan_id": plan["plan_id"],
        "active_plan_generated_at": plan["generated_at"],
        "action_valid_until": plan["action_valid_until"],
        "pending_add": deepcopy(plan.get("add_once")),
        "pending_reduce": deepcopy(plan.get("reduce_once")),
        "opened_at": executed_at_iso,
        "updated_at": executed_at_iso,
        "closed_at": None,
        "last_action_id": f'{plan["plan_id"]}:open',
    }


def apply_open_plan_update(
    current_position: dict[str, Any], plan: dict[str, Any], applied_at: datetime
) -> dict[str, Any]:
    """Apply one exact-position UPDATE and advance the durable version exactly once."""
    if current_position.get("status") != "OPEN":
        raise ValueError("UPDATE requires an OPEN position")
    if plan.get("decision") != "UPDATE":
        raise ValueError("plan decision must be UPDATE")
    if plan.get("symbol") != current_position.get("symbol"):
        raise ValueError("plan symbol must match current position symbol")
    if plan.get("target_position") != position_ref(current_position):
        raise ValueError("UPDATE must target the exact current position version")
    if plan.get("side") != current_position.get("side"):
        raise ValueError("UPDATE side must match current position side")
    if applied_at > _parse_ts(plan["action_valid_until"]):
        raise ValueError("UPDATE action is expired")

    add = plan.get("add_once")
    if add and int(current_position["qty"]) + int(add["qty"]) > 6:
        raise ValueError("UPDATE add_once may not exceed maximum 6 contracts")
    reduce = plan.get("reduce_once")
    if reduce and int(reduce["qty"]) >= int(current_position["qty"]):
        raise ValueError("reduce_once must leave at least one contract OPEN")

    updated = deepcopy(current_position)
    protection = plan.get("protection")
    if protection is not None:
        updated["stop_loss"] = float(protection["stop_loss"])
        updated["take_profit"] = float(protection["take_profit"])
    if add is not None:
        updated["pending_add"] = deepcopy(add)
    if reduce is not None:
        updated["pending_reduce"] = deepcopy(reduce)

    updated["active_plan_id"] = plan["plan_id"]
    updated["active_plan_generated_at"] = plan["generated_at"]
    updated["action_valid_until"] = plan["action_valid_until"]
    updated["position_version"] = int(current_position["position_version"]) + 1
    updated["updated_at"] = _iso_z(applied_at)
    updated["last_action_id"] = f'{plan["plan_id"]}:update'
    return updated


def execute_flat_plan(
    *,
    current_position: dict[str, Any],
    accepted_plan: dict[str, Any] | None,
    market: dict[str, Any] | None,
    executed_at: datetime,
    cycle_id: str,
    account: dict[str, Any],
    position_id: str,
    point_value: float,
) -> dict[str, Any]:
    """Execute at most one accepted OPEN action from FLAT."""
    del point_value  # OPEN realizes no P&L; multiplier is explicit for a uniform adapter API.
    if current_position.get("status") != "FLAT":
        raise ValueError("FLAT execution requires FLAT current position")
    if accepted_plan is None or accepted_plan.get("decision") != "OPEN" or market is None:
        return _no_action(current_position, account)
    if executed_at > _parse_ts(accepted_plan["action_valid_until"]):
        return _no_action(current_position, account)

    bar = _latest_bar(market)
    if bar is None:
        return _no_action(current_position, account, stale_feed=True)
    market_end = _bar_end(market, bar)
    if _parse_ts(market_end) < _parse_ts(accepted_plan["analysis_bar_end"]):
        # Recovered history may protect an existing position but never creates a historical entry.
        return _no_action(current_position, account)

    fill = fill_price_for_order(accepted_plan["entry"], accepted_plan["side"], bar)
    if fill is None:
        return _no_action(current_position, account)

    opened = open_position_from_plan(
        current_position=current_position,
        plan=accepted_plan,
        fill_price=fill,
        executed_at=executed_at,
        position_id=position_id,
    )
    execution_id = _execution_id(
        position_id=position_id,
        version_after=0,
        action="OPEN",
        market_bar_end=market_end,
        plan_id=accepted_plan["plan_id"],
    )
    opened["last_action_id"] = execution_id
    execution = _build_execution(
        execution_id=execution_id,
        cycle_id=cycle_id,
        position=opened,
        plan_id=accepted_plan["plan_id"],
        version_before=None,
        version_after=0,
        action="OPEN",
        side=_entry_execution_side(opened["side"]),
        qty=opened["qty"],
        price=fill,
        market_bar_end=market_end,
        executed_at=executed_at,
        synthetic=False,
        reason="PLAN_OPEN_TRIGGER",
        realized_pnl=0.0,
    )
    next_account = _apply_account_execution(account, execution, executed_at)
    return _transition(
        position=opened,
        account=next_account,
        execution=execution,
        outcome="OPENED",
        changed=True,
    )


def manage_open_position(
    *,
    current_position: dict[str, Any],
    accepted_plan: dict[str, Any] | None,
    market: dict[str, Any] | None,
    executed_at: datetime,
    cycle_id: str,
    account: dict[str, Any],
    point_value: float,
    stale_after_minutes: int = 15,
) -> dict[str, Any]:
    """Advance one durable OPEN position by at most one fill action.

    A valid UPDATE may first advance the durable position version. Protection remains
    authoritative even when Brain/GitHub is unavailable. Protective exits are evaluated
    before stale/EXIT and one-shot REDUCE/ADD actions.
    """
    if current_position.get("status") != "OPEN":
        raise ValueError("OPEN management requires OPEN current position")

    position = deepcopy(current_position)
    changed_without_execution = False

    if accepted_plan is not None and accepted_plan.get("decision") == "UPDATE":
        position = apply_open_plan_update(position, accepted_plan, executed_at)
        changed_without_execution = True

    bar = _latest_bar(market) if market is not None else None
    market_end = _bar_end(market, bar) if market is not None and bar is not None else None

    if bar is not None and market_end is not None and _parse_ts(market_end) >= _parse_ts(position["opened_at"]):
        protective = _protective_hit(position, bar)
        if protective is not None:
            action, price, outcome = protective
            return _close_transition(
                position=position,
                account=account,
                cycle_id=cycle_id,
                plan_id=accepted_plan.get("plan_id") if accepted_plan else position.get("active_plan_id"),
                action=action,
                outcome=outcome,
                price=price,
                market_bar_end=market_end,
                executed_at=executed_at,
                point_value=point_value,
                synthetic=False,
                reason="DURABLE_PROTECTION_TRIGGER",
            )

    stale = _is_stale(market_end, executed_at, stale_after_minutes)
    if stale:
        stop_price = float(position["stop_loss"])
        stale_bar_end = market_end or _iso_z(executed_at)
        return _close_transition(
            position=position,
            account=account,
            cycle_id=cycle_id,
            plan_id=accepted_plan.get("plan_id") if accepted_plan else position.get("active_plan_id"),
            action="STALE_FEED_FORCED_STOP",
            outcome="STALE_FEED_FORCED_STOP",
            price=stop_price,
            market_bar_end=stale_bar_end,
            executed_at=executed_at,
            point_value=point_value,
            synthetic=True,
            reason="STALE_FEED_FORCED_STOP",
            stale_feed=True,
        )

    if accepted_plan is not None and accepted_plan.get("decision") == "EXIT" and bar is not None:
        return _close_transition(
            position=position,
            account=account,
            cycle_id=cycle_id,
            plan_id=accepted_plan["plan_id"],
            action="EXIT",
            outcome="EXITED",
            price=float(bar["close"]),
            market_bar_end=market_end,
            executed_at=executed_at,
            point_value=point_value,
            synthetic=False,
            reason="PLAN_EXIT",
        )

    if executed_at > _parse_ts(position["action_valid_until"]):
        if position.get("pending_add") is not None or position.get("pending_reduce") is not None:
            position["pending_add"] = None
            position["pending_reduce"] = None
            position["position_version"] = int(position["position_version"]) + 1
            position["updated_at"] = _iso_z(executed_at)
            position["last_action_id"] = f'expire:{position["action_valid_until"]}'
            changed_without_execution = True
        return _transition(
            position=position,
            account=account,
            outcome="NO_ACTION",
            changed=changed_without_execution,
        )

    if bar is None or market_end is None:
        return _transition(
            position=position,
            account=account,
            outcome="NO_ACTION",
            changed=changed_without_execution,
            stale_feed=True,
        )

    reduce_order = position.get("pending_reduce")
    if reduce_order is not None:
        reduce_side = "SHORT" if position["side"] == "LONG" else "LONG"
        reduce_price = fill_price_for_order(reduce_order, reduce_side, bar)
        if reduce_price is not None:
            return _reduce_transition(
                position=position,
                account=account,
                cycle_id=cycle_id,
                price=reduce_price,
                qty=int(reduce_order["qty"]),
                market_bar_end=market_end,
                executed_at=executed_at,
                point_value=point_value,
            )

    add_order = position.get("pending_add")
    if add_order is not None:
        add_price = fill_price_for_order(add_order, position["side"], bar)
        if add_price is not None:
            return _add_transition(
                position=position,
                account=account,
                cycle_id=cycle_id,
                price=add_price,
                qty=int(add_order["qty"]),
                market_bar_end=market_end,
                executed_at=executed_at,
            )

    return _transition(
        position=position,
        account=account,
        outcome="NO_ACTION",
        changed=changed_without_execution,
    )


def commit_paper_transition(
    transition: dict[str, Any],
    *,
    execution_exists: Callable[[str], bool],
    append_execution: Callable[[dict[str, Any]], None],
    save_account: Callable[[dict[str, Any]], None],
    save_position: Callable[[dict[str, Any]], None],
    mirror_position: Callable[[dict[str, Any]], None],
) -> bool:
    """Persist one transition in audit/account/position order, with best-effort mirror."""
    execution = transition.get("execution")
    changed = bool(transition.get("changed", execution is not None))
    if not changed:
        return False

    if execution is not None:
        if execution_exists(execution["execution_id"]):
            return False
        append_execution(execution)
        save_account(transition["account"])

    save_position(transition["position"])
    try:
        mirror_position(transition["position"])
    except Exception:
        # Durable Position success is authoritative; GitHub is only a Brain-facing mirror.
        pass
    return True


def _protective_hit(position: dict[str, Any], bar: dict[str, Any]) -> tuple[str, float, str] | None:
    stop = float(position["stop_loss"])
    target = float(position["take_profit"])
    if position["side"] == "LONG":
        stop_hit = float(bar["low"]) <= stop
        target_hit = float(bar["high"]) >= target
    else:
        stop_hit = float(bar["high"]) >= stop
        target_hit = float(bar["low"]) <= target

    # ADVERSE_FIRST is the frozen PAPER same-bar policy.
    if stop_hit:
        return "STOP", stop, "STOPPED"
    if target_hit:
        return "TAKE_PROFIT", target, "TAKE_PROFIT"
    return None


def _close_transition(
    *,
    position: dict[str, Any],
    account: dict[str, Any],
    cycle_id: str,
    plan_id: str | None,
    action: str,
    outcome: str,
    price: float,
    market_bar_end: str,
    executed_at: datetime,
    point_value: float,
    synthetic: bool,
    reason: str,
    stale_feed: bool = False,
) -> dict[str, Any]:
    version_before = int(position["position_version"])
    version_after = version_before + 1
    qty = int(position["qty"])
    realized = _realized_pnl(position, price, qty, point_value)
    execution_id = _execution_id(
        position_id=position["position_id"],
        version_after=version_after,
        action=action,
        market_bar_end=market_bar_end,
        plan_id=plan_id,
    )
    execution = _build_execution(
        execution_id=execution_id,
        cycle_id=cycle_id,
        position=position,
        plan_id=plan_id,
        version_before=version_before,
        version_after=version_after,
        action=action,
        side=_exit_execution_side(position["side"]),
        qty=qty,
        price=price,
        market_bar_end=market_bar_end,
        executed_at=executed_at,
        synthetic=synthetic,
        reason=reason,
        realized_pnl=realized,
    )
    next_account = _apply_account_execution(account, execution, executed_at)
    flat = new_flat_position(position["symbol"], executed_at)
    return _transition(
        position=flat,
        account=next_account,
        execution=execution,
        outcome=outcome,
        changed=True,
        synthetic=synthetic,
        stale_feed=stale_feed,
    )


def _reduce_transition(
    *,
    position: dict[str, Any],
    account: dict[str, Any],
    cycle_id: str,
    price: float,
    qty: int,
    market_bar_end: str,
    executed_at: datetime,
    point_value: float,
) -> dict[str, Any]:
    if qty <= 0 or qty >= int(position["qty"]):
        raise ValueError("REDUCE must leave at least one contract OPEN")
    next_position = deepcopy(position)
    version_before = int(position["position_version"])
    version_after = version_before + 1
    next_position["qty"] = int(position["qty"]) - qty
    next_position["pending_reduce"] = None
    next_position["position_version"] = version_after
    next_position["updated_at"] = _iso_z(executed_at)

    realized = _realized_pnl(position, price, qty, point_value)
    execution_id = _execution_id(
        position_id=position["position_id"],
        version_after=version_after,
        action="REDUCE",
        market_bar_end=market_bar_end,
        plan_id=position.get("active_plan_id"),
    )
    next_position["last_action_id"] = execution_id
    execution = _build_execution(
        execution_id=execution_id,
        cycle_id=cycle_id,
        position=position,
        plan_id=position.get("active_plan_id"),
        version_before=version_before,
        version_after=version_after,
        action="REDUCE",
        side=_exit_execution_side(position["side"]),
        qty=qty,
        price=price,
        market_bar_end=market_bar_end,
        executed_at=executed_at,
        synthetic=False,
        reason="PLAN_REDUCE_TRIGGER",
        realized_pnl=realized,
    )
    return _transition(
        position=next_position,
        account=_apply_account_execution(account, execution, executed_at),
        execution=execution,
        outcome="REDUCED",
        changed=True,
    )


def _add_transition(
    *,
    position: dict[str, Any],
    account: dict[str, Any],
    cycle_id: str,
    price: float,
    qty: int,
    market_bar_end: str,
    executed_at: datetime,
) -> dict[str, Any]:
    old_qty = int(position["qty"])
    if qty <= 0 or old_qty + qty > 6:
        raise ValueError("ADD may not exceed maximum 6 contracts")
    next_position = deepcopy(position)
    version_before = int(position["position_version"])
    version_after = version_before + 1
    new_qty = old_qty + qty
    next_position["qty"] = new_qty
    next_position["avg_entry"] = (float(position["avg_entry"]) * old_qty + float(price) * qty) / new_qty
    next_position["pending_add"] = None
    next_position["position_version"] = version_after
    next_position["updated_at"] = _iso_z(executed_at)

    execution_id = _execution_id(
        position_id=position["position_id"],
        version_after=version_after,
        action="ADD",
        market_bar_end=market_bar_end,
        plan_id=position.get("active_plan_id"),
    )
    next_position["last_action_id"] = execution_id
    execution = _build_execution(
        execution_id=execution_id,
        cycle_id=cycle_id,
        position=position,
        plan_id=position.get("active_plan_id"),
        version_before=version_before,
        version_after=version_after,
        action="ADD",
        side=_entry_execution_side(position["side"]),
        qty=qty,
        price=price,
        market_bar_end=market_bar_end,
        executed_at=executed_at,
        synthetic=False,
        reason="PLAN_ADD_TRIGGER",
        realized_pnl=0.0,
    )
    return _transition(
        position=next_position,
        account=_apply_account_execution(account, execution, executed_at),
        execution=execution,
        outcome="ADDED",
        changed=True,
    )


def _build_execution(
    *,
    execution_id: str,
    cycle_id: str,
    position: dict[str, Any],
    plan_id: str | None,
    version_before: int | None,
    version_after: int,
    action: str,
    side: str,
    qty: int,
    price: float,
    market_bar_end: str,
    executed_at: datetime,
    synthetic: bool,
    reason: str,
    realized_pnl: float,
) -> dict[str, Any]:
    execution = {
        "schema": "paper_execution_log_v1",
        "execution_id": execution_id,
        "cycle_id": cycle_id,
        "symbol": position["symbol"],
        "plan_id": plan_id,
        "position_id": position["position_id"],
        "position_version_before": version_before,
        "position_version_after": version_after,
        "action": action,
        "side": side,
        "qty": int(qty),
        "price": float(price),
        "market_bar_end": market_bar_end,
        "executed_at": _iso_z(executed_at),
        "synthetic": bool(synthetic),
        "reason": reason,
        "realized_pnl_usd": float(realized_pnl),
    }
    validate_paper_execution_semantics(execution)
    return execution


def _apply_account_execution(
    account: dict[str, Any], execution: dict[str, Any], executed_at: datetime
) -> dict[str, Any]:
    next_account = deepcopy(account)
    next_account["realized_pnl_usd"] = float(account["realized_pnl_usd"]) + float(
        execution["realized_pnl_usd"]
    )
    next_account["balance_usd"] = float(next_account["starting_balance_usd"]) + float(
        next_account["realized_pnl_usd"]
    )
    next_account["updated_at"] = _iso_z(executed_at)
    next_account["last_execution_id"] = execution["execution_id"]
    validate_paper_account_state_semantics(next_account)
    return next_account


def _realized_pnl(position: dict[str, Any], price: float, qty: int, point_value: float) -> float:
    if position["side"] == "LONG":
        points = float(price) - float(position["avg_entry"])
    else:
        points = float(position["avg_entry"]) - float(price)
    return points * int(qty) * float(point_value)


def _transition(
    *,
    position: dict[str, Any],
    account: dict[str, Any],
    outcome: str,
    execution: dict[str, Any] | None = None,
    changed: bool = False,
    synthetic: bool = False,
    stale_feed: bool = False,
) -> dict[str, Any]:
    return {
        "position": position,
        "account": account,
        "execution": execution,
        "outcome": outcome,
        "changed": changed,
        "synthetic": synthetic,
        "stale_feed": stale_feed,
    }


def _no_action(
    position: dict[str, Any], account: dict[str, Any], *, stale_feed: bool = False
) -> dict[str, Any]:
    return _transition(position=position, account=account, outcome="NO_ACTION", stale_feed=stale_feed)


def _latest_bar(market: dict[str, Any] | None) -> dict[str, Any] | None:
    if not market:
        return None
    bars = market.get("bars") or []
    return bars[-1] if bars else None


def _bar_end(market: dict[str, Any], bar: dict[str, Any]) -> str:
    value = bar.get("end") or market.get("latest_bar_end")
    if not value:
        raise ValueError("market bar must include an end timestamp")
    return value


def _is_stale(market_end: str | None, now: datetime, stale_after_minutes: int) -> bool:
    if market_end is None:
        return False
    return (now - _parse_ts(market_end)).total_seconds() >= stale_after_minutes * 60


def _execution_id(
    *,
    position_id: str,
    version_after: int,
    action: str,
    market_bar_end: str,
    plan_id: str | None,
) -> str:
    plan_part = plan_id or "no-plan"
    return f"{position_id}:v{version_after}:{action}:{market_bar_end}:{plan_part}"


def _entry_execution_side(position_side: str) -> str:
    return "BUY" if position_side == "LONG" else "SELL"


def _exit_execution_side(position_side: str) -> str:
    return "SELL" if position_side == "LONG" else "BUY"


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return parsed


def _iso_z(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return value.isoformat().replace("+00:00", "Z")
