from __future__ import annotations

from decimal import Decimal
from typing import Any

from .paper_execution import (
    MAX_FEED_AGE_SECONDS,
    ROUND_TURN_COMMISSION_USD,
    OrderIntent,
    RiskDecision,
    RiskContext,
    _INSTRUMENTS,
    _decimal,
    _parse_time,
)
from .runtime_config import TradingRuntimeConfig


class ConfigurableRiskGateway:
    """Runtime-config-driven PAPER entry policy.

    The gateway intentionally reuses the already-proven execution seam and market
    mechanics from ``paper_execution`` while sourcing tunable account/risk policy
    from one validated ``TradingRuntimeConfig`` instance.
    """

    def __init__(self, config: TradingRuntimeConfig) -> None:
        if not isinstance(config, TradingRuntimeConfig) or config.paper_only is not True:
            raise ValueError("ConfigurableRiskGateway requires a validated PAPER runtime config")
        self.config = config

    def _reject(self, reason: str) -> RiskDecision:
        return RiskDecision(False, reason)

    def evaluate(
        self,
        plan: dict[str, Any],
        *,
        event_id: str,
        plan_hash: str,
        context: RiskContext,
    ) -> RiskDecision:
        cfg = self.config
        account = context.account
        market = context.market
        symbol = plan.get("symbol")
        decision = plan.get("decision")

        if account.mode != "PAPER" or account.starting_equity_usd != cfg.account.starting_equity_usd:
            return self._reject("UNAUTHORIZED_ACCOUNT")
        if not context.session_open:
            return self._reject("SESSION_CLOSED")
        if context.kill_switch:
            return self._reject("KILL_SWITCH_ACTIVE")
        if symbol not in _INSTRUMENTS or market.symbol != symbol:
            return self._reject("UNSUPPORTED_OR_MISMATCHED_SYMBOL")
        if market.environment != "PROD" or market.data_class != "REAL" or market.source != "tradingview" or not market.healthy:
            return self._reject("UNTRUSTED_FEED")
        if market.consecutive_closed_bars < 3:
            return self._reject("INSUFFICIENT_FEED_HISTORY")
        feed_age = Decimal(str((context.now - market.feed_as_of).total_seconds()))
        if feed_age < 0:
            return self._reject("FEED_TIME_IN_FUTURE")
        if feed_age > MAX_FEED_AGE_SECONDS:
            return self._reject("STALE_FEED")

        if account.daily_realized_pnl_usd <= -cfg.risk.max_daily_realized_loss_usd:
            return self._reject("DAILY_LOSS_LIMIT_REACHED")
        if account.consecutive_failures >= cfg.risk.max_consecutive_losses:
            return self._reject("CONSECUTIVE_FAILURE_LIMIT_REACHED")
        if account.open_contracts_total >= cfg.risk.max_open_micro_contracts:
            return self._reject("POSITION_LIMIT_REACHED")
        if account.entries_this_session >= cfg.risk.max_entries_per_session:
            return self._reject("SESSION_ENTRY_LIMIT_REACHED")

        if decision not in {"LONG", "SHORT"}:
            return self._reject("UNSUPPORTED_DECISION")
        if plan.get("config_version") != cfg.config_version:
            return self._reject("CONFIG_VERSION_MISMATCH")
        if plan.get("strategy_profile") != cfg.strategy.profile:
            return self._reject("STRATEGY_PROFILE_MISMATCH")

        try:
            confidence = _decimal(plan.get("confidence"), "confidence")
        except ValueError:
            return self._reject("INVALID_PLAN_CONFIDENCE")
        if confidence < cfg.strategy.min_action_confidence:
            return self._reject("CONFIDENCE_BELOW_CONFIG_MIN")

        try:
            risk_budget = _decimal(plan.get("risk_budget_usd"), "risk_budget_usd")
        except ValueError:
            return self._reject("INVALID_RISK_BUDGET")
        if risk_budget <= 0:
            return self._reject("INVALID_RISK_BUDGET")
        if risk_budget > cfg.risk.max_risk_per_trade_usd:
            return self._reject("RISK_BUDGET_EXCEEDS_CONFIG_MAX")

        action = plan.get("position_action")
        if not isinstance(action, dict):
            return self._reject("MISSING_PROTECTIVE_STOP")
        stop = action.get("protective_stop")
        if not isinstance(stop, dict) or "price" not in stop:
            return self._reject("MISSING_PROTECTIVE_STOP")
        target = action.get("take_profit")
        if not isinstance(target, dict) or "price" not in target:
            return self._reject("MISSING_TAKE_PROFIT")
        try:
            stop_price = _decimal(stop["price"], "protective_stop.price")
        except ValueError:
            return self._reject("INVALID_PROTECTIVE_STOP")
        try:
            target_price = _decimal(target["price"], "take_profit.price")
        except ValueError:
            return self._reject("INVALID_TAKE_PROFIT")

        quantity = action.get("quantity")
        if isinstance(quantity, bool) or not isinstance(quantity, int) or quantity < 1:
            return self._reject("INVALID_ORDER_QUANTITY")
        if account.open_contracts_total + quantity > cfg.risk.max_open_micro_contracts:
            return self._reject("POSITION_LIMIT_EXCEEDED")

        try:
            created_at = _parse_time(plan.get("created_at"), "created_at")
            valid_until = _parse_time(plan.get("valid_until"), "valid_until")
        except ValueError:
            return self._reject("INVALID_EXECUTION_PLAN")
        if context.now >= valid_until or market.next_bar_start >= valid_until:
            return self._reject("PLAN_EXPIRED")
        if market.next_bar_start > context.now:
            return self._reject("NEXT_BAR_NOT_OBSERVED")
        if market.next_bar_start <= created_at:
            return self._reject("NEXT_BAR_NOT_AFTER_PLAN")

        instrument = _INSTRUMENTS[symbol]
        adverse_tick = instrument["tick_size"] if decision == "LONG" else -instrument["tick_size"]
        expected_fill = market.next_bar_open + adverse_tick
        if decision == "LONG":
            if stop_price >= expected_fill:
                return self._reject("INVALID_PROTECTIVE_STOP_DIRECTION")
            if target_price <= expected_fill:
                return self._reject("INVALID_TAKE_PROFIT_DIRECTION")
        else:
            if stop_price <= expected_fill:
                return self._reject("INVALID_PROTECTIVE_STOP_DIRECTION")
            if target_price >= expected_fill:
                return self._reject("INVALID_TAKE_PROFIT_DIRECTION")

        actual_risk = (
            abs(expected_fill - stop_price) * instrument["point_value"] * quantity
            + ROUND_TURN_COMMISSION_USD * quantity
        )
        if actual_risk > cfg.risk.max_risk_per_trade_usd:
            return self._reject("MAX_TRADE_RISK_EXCEEDED")
        if actual_risk > risk_budget:
            return self._reject("PLAN_RISK_BUDGET_EXCEEDED")

        return RiskDecision(
            True,
            "RISK_APPROVED",
            OrderIntent(
                event_id=event_id,
                plan_id=plan["plan_id"],
                plan_hash=plan_hash,
                symbol=symbol,
                side=decision,
                quantity=quantity,
                expected_fill_price=expected_fill,
                protective_stop_price=stop_price,
                risk_usd=actual_risk,
                session_id=context.session_id,
                not_before=market.next_bar_start,
            ),
        )


__all__ = ["ConfigurableRiskGateway"]
