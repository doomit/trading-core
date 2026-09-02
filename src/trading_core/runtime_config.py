from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo

_SCHEMA = "trading_runtime_config_v1"
_CONFIG_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PROFILE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_SETUP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$")
_TIMEFRAME_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,15}$")
_CT = ZoneInfo("America/Chicago")

MAX_STARTING_EQUITY_USD = Decimal("10000000")
MAX_RISK_PER_TRADE_USD = Decimal("25000")
MAX_OPEN_MICRO_CONTRACTS = 100
MAX_DAILY_REALIZED_LOSS_USD = Decimal("100000")
MAX_CONSECUTIVE_LOSSES = 20
MAX_ENTRIES_PER_SESSION = 200
MAX_CONFIGURED_FEED_AGE_SECONDS = 900
DEFAULT_FEED_AGE_SECONDS = 90


def _object(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{field} must be an object")
    return value


def _identifier(value: Any, field: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise ValueError(f"{field} must be a valid identifier")
    return value


def _decimal(value: Any, field: str, *, positive: bool = False) -> Decimal:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not result.is_finite():
        raise ValueError(f"{field} must be finite")
    if positive and result <= 0:
        raise ValueError(f"{field} must be positive")
    return result


def _count(value: Any, field: str, *, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0 or value > maximum:
        raise ValueError(f"{field} must be an integer within 1..{maximum}")
    return value


def _aware_iso(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a non-empty ISO timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{field} must be an ISO timestamp") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field} must be timezone-aware")
    return parsed.isoformat()


def _json_number(value: Decimal) -> int | float:
    integral = value.to_integral_value()
    if value == integral:
        return int(integral)
    return float(value)


@dataclass(frozen=True)
class AccountConfig:
    starting_equity_usd: Decimal


@dataclass(frozen=True)
class SessionConfig:
    profile: str
    timezone: str
    maintenance_start: str
    maintenance_end: str


@dataclass(frozen=True)
class StrategyConfig:
    profile: str
    analysis_timeframe: str
    min_action_confidence: Decimal
    setup_families: tuple[str, ...]


@dataclass(frozen=True)
class RiskConfig:
    target_risk_per_trade_usd: Decimal
    max_risk_per_trade_usd: Decimal
    max_open_micro_contracts: int
    max_daily_realized_loss_usd: Decimal
    max_consecutive_losses: int
    max_entries_per_session: int
    configured_max_feed_age_seconds: int | None = None

    @property
    def max_feed_age_seconds(self) -> int:
        return (
            self.configured_max_feed_age_seconds
            if self.configured_max_feed_age_seconds is not None
            else DEFAULT_FEED_AGE_SECONDS
        )


@dataclass(frozen=True)
class SessionState:
    session_id: str
    session_open: bool


@dataclass(frozen=True)
class TradingRuntimeConfig:
    schema: str
    config_version: str
    created_at: str
    paper_only: bool
    account: AccountConfig
    session: SessionConfig
    strategy: StrategyConfig
    risk: RiskConfig

    @classmethod
    def from_document(cls, document: dict[str, Any]) -> "TradingRuntimeConfig":
        if not isinstance(document, dict):
            raise ValueError("runtime config must be an object")
        if document.get("schema") != _SCHEMA:
            raise ValueError(f"runtime config schema must be {_SCHEMA}")
        config_version = _identifier(document.get("config_version"), "config_version", _CONFIG_ID)
        created_at = _aware_iso(document.get("created_at"), "created_at")
        if document.get("paper_only") is not True:
            raise ValueError("runtime config must be PAPER-only")

        account_doc = _object(document.get("account"), "account")
        starting_equity = _decimal(account_doc.get("starting_equity_usd"), "account.starting_equity_usd", positive=True)
        if starting_equity > MAX_STARTING_EQUITY_USD:
            raise ValueError("account.starting_equity_usd exceeds compiled PAPER envelope")

        session_doc = _object(document.get("session"), "session")
        if session_doc.get("profile") != "CME_INDEX_23H":
            raise ValueError("session.profile must be CME_INDEX_23H")
        if session_doc.get("timezone") != "America/Chicago":
            raise ValueError("session.timezone must be America/Chicago")
        if session_doc.get("maintenance_start") != "16:00" or session_doc.get("maintenance_end") != "17:00":
            raise ValueError("CME maintenance window is fixed at 16:00-17:00 America/Chicago")
        session = SessionConfig(
            profile="CME_INDEX_23H",
            timezone="America/Chicago",
            maintenance_start="16:00",
            maintenance_end="17:00",
        )

        strategy_doc = _object(document.get("strategy"), "strategy")
        strategy_profile = _identifier(strategy_doc.get("profile"), "strategy.profile", _PROFILE_ID)
        timeframe = _identifier(strategy_doc.get("analysis_timeframe"), "strategy.analysis_timeframe", _TIMEFRAME_ID)
        min_confidence = _decimal(strategy_doc.get("min_action_confidence"), "strategy.min_action_confidence")
        if min_confidence < 0 or min_confidence > 1:
            raise ValueError("strategy.min_action_confidence must be within 0..1")
        raw_setups = strategy_doc.get("setup_families")
        if not isinstance(raw_setups, list) or not raw_setups or len(raw_setups) > 32:
            raise ValueError("strategy.setup_families must be a non-empty array of at most 32 identifiers")
        setup_families = tuple(_identifier(item, "strategy.setup_families[]", _SETUP_ID) for item in raw_setups)
        if len(set(setup_families)) != len(setup_families):
            raise ValueError("strategy.setup_families must not contain duplicates")
        strategy = StrategyConfig(strategy_profile, timeframe, min_confidence, setup_families)

        risk_doc = _object(document.get("risk"), "risk")
        target_risk = _decimal(risk_doc.get("target_risk_per_trade_usd"), "risk.target_risk_per_trade_usd", positive=True)
        max_risk = _decimal(risk_doc.get("max_risk_per_trade_usd"), "risk.max_risk_per_trade_usd", positive=True)
        if target_risk > max_risk:
            raise ValueError("risk.target_risk_per_trade_usd cannot exceed risk.max_risk_per_trade_usd")
        if max_risk > MAX_RISK_PER_TRADE_USD:
            raise ValueError("risk.max_risk_per_trade_usd exceeds compiled PAPER envelope")
        max_open = _count(risk_doc.get("max_open_micro_contracts"), "risk.max_open_micro_contracts", maximum=MAX_OPEN_MICRO_CONTRACTS)
        daily_loss = _decimal(risk_doc.get("max_daily_realized_loss_usd"), "risk.max_daily_realized_loss_usd", positive=True)
        if daily_loss > MAX_DAILY_REALIZED_LOSS_USD:
            raise ValueError("risk.max_daily_realized_loss_usd exceeds compiled PAPER envelope")
        max_losses = _count(risk_doc.get("max_consecutive_losses"), "risk.max_consecutive_losses", maximum=MAX_CONSECUTIVE_LOSSES)
        max_entries = _count(risk_doc.get("max_entries_per_session"), "risk.max_entries_per_session", maximum=MAX_ENTRIES_PER_SESSION)
        raw_feed_age = risk_doc.get("max_feed_age_seconds")
        max_feed_age = None
        if raw_feed_age is not None:
            max_feed_age = _count(
                raw_feed_age,
                "risk.max_feed_age_seconds",
                maximum=MAX_CONFIGURED_FEED_AGE_SECONDS,
            )
        risk = RiskConfig(
            target_risk,
            max_risk,
            max_open,
            daily_loss,
            max_losses,
            max_entries,
            max_feed_age,
        )

        return cls(
            schema=_SCHEMA,
            config_version=config_version,
            created_at=created_at,
            paper_only=True,
            account=AccountConfig(starting_equity),
            session=session,
            strategy=strategy,
            risk=risk,
        )

    def to_document(self) -> dict[str, Any]:
        risk_document = {
            "target_risk_per_trade_usd": _json_number(self.risk.target_risk_per_trade_usd),
            "max_risk_per_trade_usd": _json_number(self.risk.max_risk_per_trade_usd),
            "max_open_micro_contracts": self.risk.max_open_micro_contracts,
            "max_daily_realized_loss_usd": _json_number(self.risk.max_daily_realized_loss_usd),
            "max_consecutive_losses": self.risk.max_consecutive_losses,
            "max_entries_per_session": self.risk.max_entries_per_session,
        }
        if self.risk.configured_max_feed_age_seconds is not None:
            risk_document["max_feed_age_seconds"] = self.risk.configured_max_feed_age_seconds
        return {
            "schema": self.schema,
            "config_version": self.config_version,
            "created_at": self.created_at,
            "paper_only": self.paper_only,
            "account": {"starting_equity_usd": _json_number(self.account.starting_equity_usd)},
            "session": {
                "profile": self.session.profile,
                "timezone": self.session.timezone,
                "maintenance_start": self.session.maintenance_start,
                "maintenance_end": self.session.maintenance_end,
            },
            "strategy": {
                "profile": self.strategy.profile,
                "analysis_timeframe": self.strategy.analysis_timeframe,
                "min_action_confidence": _json_number(self.strategy.min_action_confidence),
                "setup_families": list(self.strategy.setup_families),
            },
            "risk": risk_document,
        }


def _trade_date(local: datetime) -> date:
    if local.hour >= 17:
        return local.date() + timedelta(days=1)
    return local.date()


def cme_session_state(now: datetime, config: TradingRuntimeConfig) -> SessionState:
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    if config.session.profile != "CME_INDEX_23H":
        raise ValueError("unsupported session profile")
    local = now.astimezone(_CT)
    minute = local.hour * 60 + local.minute
    weekday = local.weekday()  # Monday=0, Sunday=6

    if weekday == 5:  # Saturday
        is_open = False
    elif weekday == 6:  # Sunday weekly reopen at 17:00
        is_open = minute >= 17 * 60
    elif weekday == 4:  # Friday closes for weekend at 16:00
        is_open = minute < 16 * 60
    else:  # Monday-Thursday: 23h with 16:00-17:00 maintenance
        is_open = minute < 16 * 60 or minute >= 17 * 60

    return SessionState(
        session_id=f"CME-{_trade_date(local).isoformat()}",
        session_open=is_open,
    )


__all__ = [
    "AccountConfig",
    "RiskConfig",
    "SessionConfig",
    "SessionState",
    "StrategyConfig",
    "TradingRuntimeConfig",
    "cme_session_state",
]
