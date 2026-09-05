from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Protocol


AUTHORIZED_STARTING_EQUITY_USD = Decimal("50000.00")
MAX_DAILY_LOSS_USD = Decimal("600.00")
MAX_RISK_PER_TRADE_USD = Decimal("150.00")
MAX_CONSECUTIVE_FAILURES = 3
MAX_ENTRIES_PER_SESSION = 8
MAX_OPEN_MICRO_CONTRACTS = 1
MAX_FEED_AGE_SECONDS = Decimal("90")
ROUND_TURN_COMMISSION_USD = Decimal("2.50")

_INSTRUMENTS = {
    "MES1!": {"tick_size": Decimal("0.25"), "point_value": Decimal("5.00")},
    "MNQ1!": {"tick_size": Decimal("0.25"), "point_value": Decimal("2.00")},
}


def _decimal(value: Any, field: str) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    return result


def _aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")


def _parse_time(value: Any, field: str) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    _aware(parsed, field)
    return parsed


def canonical_plan_hash(plan: dict[str, Any]) -> str:
    encoded = json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class AccountState:
    mode: str
    starting_equity_usd: Decimal
    equity_usd: Decimal
    daily_realized_pnl_usd: Decimal
    consecutive_failures: int
    open_contracts_total: int
    entries_this_session: int = 0
    reserved_contracts_total: int = 0

    def __post_init__(self) -> None:
        for field in ("starting_equity_usd", "equity_usd", "daily_realized_pnl_usd"):
            object.__setattr__(self, field, _decimal(getattr(self, field), field))
        for field in ("consecutive_failures", "open_contracts_total", "entries_this_session", "reserved_contracts_total"):
            value = getattr(self, field)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{field} must be a nonnegative integer")


@dataclass(frozen=True)
class MarketSnapshot:
    symbol: str
    feed_as_of: datetime
    next_bar_start: datetime
    next_bar_open: Decimal
    environment: str
    data_class: str
    source: str
    healthy: bool
    consecutive_closed_bars: int

    def __post_init__(self) -> None:
        _aware(self.feed_as_of, "feed_as_of")
        _aware(self.next_bar_start, "next_bar_start")
        object.__setattr__(self, "next_bar_open", _decimal(self.next_bar_open, "next_bar_open"))
        if self.next_bar_open <= 0:
            raise ValueError("next_bar_open must be positive")
        if isinstance(self.consecutive_closed_bars, bool) or not isinstance(self.consecutive_closed_bars, int):
            raise ValueError("consecutive_closed_bars must be an integer")


@dataclass(frozen=True)
class RiskContext:
    now: datetime
    session_id: str
    session_open: bool
    kill_switch: bool
    account: AccountState
    market: MarketSnapshot
    paused: bool = False

    def __post_init__(self) -> None:
        _aware(self.now, "now")
        if not isinstance(self.session_id, str) or not self.session_id:
            raise ValueError("session_id must be a non-empty string")
        if not isinstance(self.session_open, bool) or not isinstance(self.kill_switch, bool) or not isinstance(self.paused, bool):
            raise ValueError("session flags must be booleans")


@dataclass(frozen=True)
class OrderIntent:
    event_id: str
    plan_id: str
    plan_hash: str
    symbol: str
    side: str
    quantity: int
    expected_fill_price: Decimal
    protective_stop_price: Decimal
    risk_usd: Decimal
    session_id: str
    not_before: datetime


@dataclass(frozen=True)
class PaperOrder:
    order_id: str
    symbol: str
    side: str
    quantity: int
    protective_stop_price: Decimal
    submitted_at: datetime
    order_type: str = "MARKET"
    status: str = "FILLED"
    limit_price: Decimal | None = None
    stop_price: Decimal | None = None


@dataclass(frozen=True)
class PaperFill:
    fill_id: str
    order_id: str
    price: Decimal
    quantity: int
    occurred_at: datetime
    reference_price: Decimal | None = None
    slippage_points: Decimal = Decimal("0")
    commission_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class PaperPositionRecord:
    position_id: str
    event_id: str
    plan_id: str
    order_id: str
    entry_fill_id: str
    symbol: str
    side: str
    quantity: int
    entry_price: Decimal
    opened_at: datetime
    status: str = "OPEN"


@dataclass(frozen=True)
class PaperTrade:
    trade_id: str
    event_id: str
    plan_id: str
    order_id: str
    fill_id: str
    position_id: str
    symbol: str
    side: str
    quantity: int
    price: Decimal
    occurred_at: datetime
    role: str
    slippage_points: Decimal = Decimal("0")
    commission_usd: Decimal = Decimal("0")


@dataclass(frozen=True)
class RiskDecision:
    approved: bool
    reason_code: str
    intent: OrderIntent | None = None


@dataclass(frozen=True)
class ExecutionResult:
    event_id: str
    plan_id: str
    plan_hash: str
    status: str
    reason_code: str
    terminal: bool
    receipts: tuple[dict[str, Any], ...]
    order: PaperOrder | None = None
    fill: PaperFill | None = None
    position: PaperPositionRecord | None = None
    trade: PaperTrade | None = None
    exit_trades: tuple[PaperTrade, ...] = ()


class PaperBroker(Protocol):
    def submit(self, intent: OrderIntent, market_state: MarketSnapshot) -> tuple[PaperOrder, PaperFill]: ...


class ExecutionConflict(RuntimeError):
    pass


class ExecutionLedger:
    """Thread-safe exactly-once-in-effect ledger used behind the narrow executor seam."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._results: dict[str, tuple[str, str, ExecutionResult]] = {}

    def execute_once(
        self,
        *,
        plan_id: str,
        event_id: str,
        plan_hash: str,
        operation: Callable[[], ExecutionResult],
    ) -> ExecutionResult:
        with self._lock:
            existing = self._results.get(plan_id)
            if existing is not None:
                existing_event_id, existing_hash, existing_result = existing
                if existing_event_id != event_id or existing_hash != plan_hash:
                    raise ExecutionConflict("plan_id is already bound to another execution identity")
                return existing_result
            result = operation()
            self._results[plan_id] = (event_id, plan_hash, result)
            return result


class RiskGateway:
    def evaluate(self, plan: dict[str, Any], *, event_id: str, plan_hash: str, context: RiskContext) -> RiskDecision:
        account = context.account
        market = context.market
        symbol = plan.get("symbol")
        decision = plan.get("decision")
        if account.mode != "PAPER" or account.starting_equity_usd != AUTHORIZED_STARTING_EQUITY_USD:
            return RiskDecision(False, "UNAUTHORIZED_ACCOUNT")
        if not context.session_open:
            return RiskDecision(False, "SESSION_CLOSED")
        if context.kill_switch:
            return RiskDecision(False, "KILL_SWITCH_ACTIVE")
        if context.paused:
            return RiskDecision(False, "PAUSE_ACTIVE")
        if symbol not in _INSTRUMENTS or market.symbol != symbol:
            return RiskDecision(False, "UNSUPPORTED_OR_MISMATCHED_SYMBOL")
        if market.environment != "PROD" or market.data_class != "REAL" or market.source != "tradingview" or not market.healthy:
            return RiskDecision(False, "UNTRUSTED_FEED")
        if market.consecutive_closed_bars < 3:
            return RiskDecision(False, "INSUFFICIENT_FEED_HISTORY")
        feed_age = Decimal(str((context.now - market.feed_as_of).total_seconds()))
        if feed_age < 0:
            return RiskDecision(False, "FEED_TIME_IN_FUTURE")
        if feed_age > MAX_FEED_AGE_SECONDS:
            return RiskDecision(False, "STALE_FEED")
        if account.daily_realized_pnl_usd <= -MAX_DAILY_LOSS_USD:
            return RiskDecision(False, "DAILY_LOSS_LIMIT_REACHED")
        if account.consecutive_failures >= MAX_CONSECUTIVE_FAILURES:
            return RiskDecision(False, "CONSECUTIVE_FAILURE_LIMIT_REACHED")
        if account.reserved_contracts_total > 0 and account.open_contracts_total + account.reserved_contracts_total >= MAX_OPEN_MICRO_CONTRACTS:
            return RiskDecision(False, "OPEN_ORDER_CONFLICT")
        if account.open_contracts_total >= MAX_OPEN_MICRO_CONTRACTS:
            return RiskDecision(False, "POSITION_LIMIT_REACHED")
        if account.entries_this_session >= MAX_ENTRIES_PER_SESSION:
            return RiskDecision(False, "SESSION_ENTRY_LIMIT_REACHED")
        if decision not in {"LONG", "SHORT"}:
            return RiskDecision(False, "UNSUPPORTED_DECISION")
        action = plan.get("position_action")
        if not isinstance(action, dict):
            return RiskDecision(False, "MISSING_PROTECTIVE_STOP")
        stop = action.get("protective_stop")
        if not isinstance(stop, dict) or "price" not in stop:
            return RiskDecision(False, "MISSING_PROTECTIVE_STOP")
        target = action.get("take_profit")
        if not isinstance(target, dict) or "price" not in target:
            return RiskDecision(False, "MISSING_TAKE_PROFIT")
        try:
            stop_price = _decimal(stop["price"], "protective_stop.price")
        except ValueError:
            return RiskDecision(False, "INVALID_PROTECTIVE_STOP")
        try:
            target_price = _decimal(target["price"], "take_profit.price")
        except ValueError:
            return RiskDecision(False, "INVALID_TAKE_PROFIT")
        quantity = action.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return RiskDecision(False, "INVALID_ORDER_QUANTITY")
        if quantity > MAX_OPEN_MICRO_CONTRACTS:
            return RiskDecision(False, "POSITION_LIMIT_EXCEEDED")
        created_at = _parse_time(plan.get("created_at"), "created_at")
        valid_until = _parse_time(plan.get("valid_until"), "valid_until")
        if context.now >= valid_until or market.next_bar_start >= valid_until:
            return RiskDecision(False, "PLAN_EXPIRED")
        if market.next_bar_start > context.now:
            return RiskDecision(False, "NEXT_BAR_NOT_OBSERVED")
        if market.next_bar_start <= created_at:
            return RiskDecision(False, "NEXT_BAR_NOT_AFTER_PLAN")
        instrument = _INSTRUMENTS[symbol]
        adverse_tick = instrument["tick_size"] if decision == "LONG" else -instrument["tick_size"]
        expected_fill = market.next_bar_open + adverse_tick
        if decision == "LONG" and stop_price >= expected_fill:
            return RiskDecision(False, "INVALID_PROTECTIVE_STOP_DIRECTION")
        if decision == "SHORT" and stop_price <= expected_fill:
            return RiskDecision(False, "INVALID_PROTECTIVE_STOP_DIRECTION")
        if decision == "LONG" and target_price <= expected_fill:
            return RiskDecision(False, "INVALID_TAKE_PROFIT_DIRECTION")
        if decision == "SHORT" and target_price >= expected_fill:
            return RiskDecision(False, "INVALID_TAKE_PROFIT_DIRECTION")
        risk_usd = abs(expected_fill - stop_price) * instrument["point_value"] * quantity + ROUND_TURN_COMMISSION_USD * quantity
        if risk_usd > MAX_RISK_PER_TRADE_USD:
            return RiskDecision(False, "MAX_TRADE_RISK_EXCEEDED")
        return RiskDecision(True, "RISK_APPROVED", OrderIntent(event_id, plan["plan_id"], plan_hash, symbol, decision, quantity, expected_fill, stop_price, risk_usd, context.session_id, market.next_bar_start))


class DeterministicPaperBroker:
    """Credential-free paper adapter with conservative, reproducible fills."""

    def __init__(self) -> None:
        self._pending_lock = threading.Lock()
        self._filled_pending_limits: dict[str, PaperOrder] = {}

    def submit(self, intent: OrderIntent, market_state: MarketSnapshot) -> tuple[PaperOrder, PaperFill]:
        if intent.symbol != market_state.symbol:
            raise ValueError("order intent and market symbol do not match")
        if market_state.next_bar_start < intent.not_before:
            raise ValueError("paper fill precedes the allowed next bar")
        identity = hashlib.sha256(f"{intent.event_id}|{intent.plan_id}|{intent.plan_hash}".encode()).hexdigest()
        order_id = f"paper-order:{identity[:32]}"
        order = PaperOrder(order_id, intent.symbol, intent.side, intent.quantity, intent.protective_stop_price, market_state.next_bar_start)
        slippage_points = intent.expected_fill_price - market_state.next_bar_open
        commission_usd = ROUND_TURN_COMMISSION_USD * intent.quantity / Decimal("2")
        fill = PaperFill(
            f"paper-fill:{identity[:32]}",
            order_id,
            intent.expected_fill_price,
            intent.quantity,
            market_state.next_bar_start,
            market_state.next_bar_open,
            slippage_points,
            commission_usd,
        )
        return order, fill

    def submit_limit(
        self,
        intent: OrderIntent,
        market_state: MarketSnapshot,
        *,
        limit_price: Decimal,
    ) -> tuple[PaperOrder, PaperFill | None]:
        if intent.symbol != market_state.symbol:
            raise ValueError("order intent and market symbol do not match")
        if market_state.next_bar_start < intent.not_before:
            raise ValueError("paper order precedes the allowed next bar")
        limit = _decimal(limit_price, "limit_price")
        if limit <= 0:
            raise ValueError("limit_price must be positive")
        identity = hashlib.sha256(
            f"{intent.event_id}|{intent.plan_id}|{intent.plan_hash}|LIMIT|{limit}".encode()
        ).hexdigest()
        order = PaperOrder(
            f"paper-order:{identity[:32]}",
            intent.symbol,
            intent.side,
            intent.quantity,
            intent.protective_stop_price,
            market_state.next_bar_start,
            "LIMIT",
            "PENDING",
            limit,
        )
        return order, None

    def submit_stop(
        self,
        intent: OrderIntent,
        market_state: MarketSnapshot,
        *,
        stop_price: Decimal,
    ) -> tuple[PaperOrder, PaperFill | None]:
        if intent.symbol != market_state.symbol:
            raise ValueError("order intent and market symbol do not match")
        if market_state.next_bar_start < intent.not_before:
            raise ValueError("paper order precedes the allowed next bar")
        stop = _decimal(stop_price, "stop_price")
        if stop <= 0:
            raise ValueError("stop_price must be positive")
        identity = hashlib.sha256(
            f"{intent.event_id}|{intent.plan_id}|{intent.plan_hash}|STOP|{stop}".encode()
        ).hexdigest()
        order = PaperOrder(
            order_id=f"paper-order:{identity[:32]}",
            symbol=intent.symbol,
            side=intent.side,
            quantity=intent.quantity,
            protective_stop_price=intent.protective_stop_price,
            submitted_at=market_state.next_bar_start,
            order_type="STOP",
            status="PENDING",
            stop_price=stop,
        )
        return order, None

    def process_pending_limit(
        self,
        order: PaperOrder,
        intent: OrderIntent,
        *,
        bar_low: Decimal,
        bar_high: Decimal,
        occurred_at: datetime,
    ) -> tuple[PaperOrder, PaperFill | None]:
        if order.order_type != "LIMIT" or order.status != "PENDING" or order.limit_price is None:
            raise ValueError("order must be a pending LIMIT")
        if order.symbol != intent.symbol or order.side != intent.side or order.quantity != intent.quantity:
            raise ValueError("pending order and intent do not match")
        if order.side != "LONG":
            raise ValueError("pending LIMIT side is not yet supported")
        _aware(occurred_at, "occurred_at")
        low = _decimal(bar_low, "bar_low")
        high = _decimal(bar_high, "bar_high")
        if low > high:
            raise ValueError("bar_low must not exceed bar_high")
        with self._pending_lock:
            existing = self._filled_pending_limits.get(order.order_id)
            if existing is not None:
                return existing, None
            if low > order.limit_price:
                return order, None
            filled = PaperOrder(
                order.order_id,
                order.symbol,
                order.side,
                order.quantity,
                order.protective_stop_price,
                order.submitted_at,
                order.order_type,
                "FILLED",
                order.limit_price,
            )
            identity = hashlib.sha256(f"{order.order_id}|{occurred_at.isoformat()}|LIMIT".encode()).hexdigest()
            commission_usd = ROUND_TURN_COMMISSION_USD * order.quantity / Decimal("2")
            fill = PaperFill(
                f"paper-fill:{identity[:32]}",
                order.order_id,
                order.limit_price,
                order.quantity,
                occurred_at,
                order.limit_price,
                Decimal("0"),
                commission_usd,
            )
            self._filled_pending_limits[order.order_id] = filled
            return filled, fill


def _receipt(*, event_id: str, plan_id: str, stage: str, status: str, source: str, occurred_at: datetime, reason_code: str, decision: str | None = None) -> dict[str, Any]:
    identity = hashlib.sha256(f"{event_id}|{plan_id}|{stage}|{source}".encode()).hexdigest()
    receipt: dict[str, Any] = {"schema":"runtime_activity_v1","receipt_id":f"paper:{identity[:32]}","event_id":event_id,"plan_id":plan_id,"stage":stage,"status":status,"occurred_at":occurred_at.isoformat(),"source":source,"reason_code":reason_code}
    if decision is not None:
        receipt["details"] = {"decision": decision}
    return receipt


def _rejected_before_claim(*, event_id: str, plan_id: str, plan_hash: str, context: RiskContext, reason_code: str) -> ExecutionResult:
    return ExecutionResult(event_id, plan_id, plan_hash, "REJECTED", reason_code, True, (_receipt(event_id=event_id, plan_id=plan_id, stage="EXECUTOR_RECEIVED", status="REJECTED", source="azure_executor", occurred_at=context.now, reason_code=reason_code),))


def execute_reserved_plan(
    plan: dict[str, Any],
    *,
    event_id: str,
    reservation_plan_hash: str,
    context: RiskContext,
    risk_gateway: RiskGateway,
    broker: PaperBroker,
    ledger: ExecutionLedger,
    pre_submit_guard: Callable[[OrderIntent], str | None] | None = None,
) -> ExecutionResult:
    """Consume one validated/reserved trading_plan_v1 through paper execution."""
    if not isinstance(plan, dict):
        raise ValueError("plan must be an object")
    plan_id = plan.get("plan_id")
    plan_hash = canonical_plan_hash(plan)
    if not isinstance(event_id, str) or not event_id or not isinstance(plan_id, str) or not plan_id:
        raise ValueError("event_id and plan_id must be non-empty strings")
    if plan.get("schema") != "trading_plan_v1" or plan.get("trigger_event_id") != event_id or plan_id != event_id:
        return _rejected_before_claim(event_id=event_id, plan_id=plan_id, plan_hash=plan_hash, context=context, reason_code="INVALID_EXECUTION_IDENTITY")
    if reservation_plan_hash != plan_hash:
        return _rejected_before_claim(event_id=event_id, plan_id=plan_id, plan_hash=plan_hash, context=context, reason_code="RESERVATION_HASH_MISMATCH")
    def operation() -> ExecutionResult:
        decision = plan.get("decision")
        received = _receipt(event_id=event_id, plan_id=plan_id, stage="EXECUTOR_RECEIVED", status="PASS", source="azure_executor", occurred_at=context.now, reason_code="PLAN_RESERVED_FOR_EXECUTION", decision=decision if isinstance(decision, str) else None)
        if decision in {"NO_TRADE", "HOLD"}:
            reason = f"PLAN_{decision}"
            completed = _receipt(event_id=event_id, plan_id=plan_id, stage="COMPLETED", status="PASS", source="azure_executor", occurred_at=context.now, reason_code=reason, decision=decision)
            return ExecutionResult(event_id, plan_id, plan_hash, "NO_EXECUTION", reason, True, (received, completed))
        try:
            risk = risk_gateway.evaluate(plan, event_id=event_id, plan_hash=plan_hash, context=context)
        except ValueError:
            risk = RiskDecision(False, "INVALID_EXECUTION_PLAN")
        risk_receipt = _receipt(event_id=event_id, plan_id=plan_id, stage="RISK_DECIDED", status="PASS" if risk.approved else "REJECTED", source="risk_gateway", occurred_at=context.now, reason_code=risk.reason_code, decision=decision if isinstance(decision, str) else None)
        if not risk.approved or risk.intent is None:
            return ExecutionResult(event_id, plan_id, plan_hash, "REJECTED", risk.reason_code, True, (received, risk_receipt))
        if pre_submit_guard is not None:
            admission_reason = pre_submit_guard(risk.intent)
            if admission_reason:
                admission_receipt = _receipt(event_id=event_id, plan_id=plan_id, stage="PRE_SUBMIT_ADMISSION", status="REJECTED", source="risk_gateway", occurred_at=context.now, reason_code=admission_reason, decision=decision if isinstance(decision, str) else None)
                return ExecutionResult(event_id, plan_id, plan_hash, "REJECTED", admission_reason, True, (received, risk_receipt, admission_receipt))
        order, fill = broker.submit(risk.intent, context.market)
        record_identity = hashlib.sha256(f"{event_id}|{plan_id}|{plan_hash}|{order.order_id}|{fill.fill_id}".encode()).hexdigest()
        position = PaperPositionRecord(f"paper-position:{record_identity[:32]}", event_id, plan_id, order.order_id, fill.fill_id, order.symbol, order.side, fill.quantity, fill.price, fill.occurred_at)
        trade = PaperTrade(
            f"paper-trade:{record_identity[:32]}",
            event_id,
            plan_id,
            order.order_id,
            fill.fill_id,
            position.position_id,
            order.symbol,
            order.side,
            fill.quantity,
            fill.price,
            fill.occurred_at,
            "ENTRY",
            fill.slippage_points,
            fill.commission_usd,
        )
        ordered = _receipt(event_id=event_id, plan_id=plan_id, stage="PAPER_ORDERED", status="PASS", source="paper_broker", occurred_at=order.submitted_at, reason_code="PAPER_ORDER_CREATED", decision=decision)
        filled = _receipt(event_id=event_id, plan_id=plan_id, stage="PAPER_FILLED_OR_REJECTED", status="PASS", source="paper_broker", occurred_at=fill.occurred_at, reason_code="PAPER_FILL_CREATED", decision=decision)
        return ExecutionResult(event_id, plan_id, plan_hash, "FILLED", "PAPER_ENTRY_FILLED_POSITION_OPEN", False, (received, risk_receipt, ordered, filled), order, fill, position, trade)
    try:
        return ledger.execute_once(plan_id=plan_id, event_id=event_id, plan_hash=plan_hash, operation=operation)
    except ExecutionConflict:
        return _rejected_before_claim(event_id=event_id, plan_id=plan_id, plan_hash=plan_hash, context=context, reason_code="PLAN_ID_EXECUTION_CONFLICT")


__all__ = ["AccountState","DeterministicPaperBroker","ExecutionLedger","ExecutionResult","MarketSnapshot","OrderIntent","PaperBroker","PaperFill","PaperOrder","PaperPositionRecord","PaperTrade","RiskContext","RiskDecision","RiskGateway","canonical_plan_hash","execute_reserved_plan"]