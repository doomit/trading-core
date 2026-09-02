from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from trading_core.paper_execution import (
    AccountState,
    DeterministicPaperBroker,
    ExecutionLedger,
    MarketSnapshot,
    RiskContext,
    RiskGateway,
    canonical_plan_hash,
    execute_reserved_plan,
)

NOW = datetime(2026, 8, 31, 14, 31, tzinfo=timezone.utc)
EVENT_ID = "evt_mes_20260831T143000Z_0001"

def plan(decision="LONG", **overrides):
    document = {"schema":"trading_plan_v1","plan_id":EVENT_ID,"trigger_event_id":EVENT_ID,"created_at":(NOW-timedelta(seconds=10)).isoformat(),"valid_until":(NOW+timedelta(seconds=50)).isoformat(),"symbol":"MES1!","decision":decision,"confidence":0.8,"analysis_summary":["deterministic paper-execution test"],"position_action":{"quantity":1,"protective_stop":{"price":"5990.00"}}}
    document.update(overrides); return document

def account(**overrides):
    values={"mode":"PAPER","starting_equity_usd":Decimal("50000.00"),"equity_usd":Decimal("50000.00"),"daily_realized_pnl_usd":Decimal("0.00"),"consecutive_failures":0,"open_contracts_total":0}; values.update(overrides); return AccountState(**values)

def market(**overrides):
    values={"symbol":"MES1!","feed_as_of":NOW-timedelta(seconds=5),"next_bar_start":NOW,"next_bar_open":Decimal("6000.00"),"environment":"PROD","data_class":"REAL","source":"tradingview","healthy":True,"consecutive_closed_bars":3}; values.update(overrides); return MarketSnapshot(**values)

def context(*,account_state=None,market_state=None,**overrides):
    values={"now":NOW,"session_id":"CME-2026-08-31","session_open":True,"kill_switch":False,"account":account_state or account(),"market":market_state or market()}; values.update(overrides); return RiskContext(**values)

class CountingPaperBroker(DeterministicPaperBroker):
    def __init__(self): super().__init__(); self.calls=0
    def submit(self,intent,market_state): self.calls+=1; return super().submit(intent,market_state)

def execute(document,*,risk_context=None,ledger=None,broker=None,reservation_hash=None):
    return execute_reserved_plan(document,event_id=EVENT_ID,reservation_plan_hash=reservation_hash or canonical_plan_hash(document),context=risk_context or context(),risk_gateway=RiskGateway(),broker=broker or DeterministicPaperBroker(),ledger=ledger or ExecutionLedger())

@pytest.mark.parametrize("decision",["NO_TRADE","HOLD"])
def test_no_trade_and_hold_are_terminal_without_order_or_fill(decision):
    broker=CountingPaperBroker(); result=execute(plan(decision,position_action=None),broker=broker)
    assert result.terminal is True; assert result.status=="NO_EXECUTION"; assert result.reason_code==f"PLAN_{decision}"; assert result.order is None; assert result.fill is None; assert broker.calls==0
    assert [r["stage"] for r in result.receipts]==["EXECUTOR_RECEIVED","COMPLETED"]

@pytest.mark.parametrize("decision",["LONG","SHORT"])
def test_directional_plan_requires_explicit_protective_stop(decision):
    result=execute(plan(decision,position_action={"quantity":1})); assert result.status=="REJECTED"; assert result.reason_code=="MISSING_PROTECTIVE_STOP"; assert result.order is None; assert result.fill is None

@pytest.mark.parametrize("decision",["LONG","SHORT"])
def test_directional_plan_requires_take_profit_for_terminal_bracket_lifecycle(decision):
    broker=CountingPaperBroker(); result=execute(plan(decision),broker=broker)
    assert result.status=="REJECTED"; assert result.reason_code=="MISSING_TAKE_PROFIT"; assert result.order is None; assert result.fill is None; assert broker.calls==0

def test_directional_plan_rejects_non_numeric_take_profit_before_broker_execution():
    broker=CountingPaperBroker(); result=execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"not-a-price"}}),broker=broker)
    assert result.status=="REJECTED"; assert result.reason_code=="INVALID_TAKE_PROFIT"; assert result.order is None; assert result.fill is None; assert broker.calls==0

@pytest.mark.parametrize("decision,stop,target",[("LONG","5990.00","5999.00"),("SHORT","6010.00","6001.00")])
def test_directional_plan_rejects_wrong_side_take_profit_before_broker_execution(decision,stop,target):
    broker=CountingPaperBroker(); result=execute(plan(decision,position_action={"quantity":1,"protective_stop":{"price":stop},"take_profit":{"price":target}}),broker=broker)
    assert result.status=="REJECTED"; assert result.reason_code=="INVALID_TAKE_PROFIT_DIRECTION"; assert result.order is None; assert result.fill is None; assert broker.calls==0

@pytest.mark.parametrize("risk_context,reason",[(context(market_state=market(feed_as_of=NOW-timedelta(seconds=91))),"STALE_FEED"),(context(session_open=False),"SESSION_CLOSED"),(context(kill_switch=True),"KILL_SWITCH_ACTIVE"),(context(account_state=account(daily_realized_pnl_usd=Decimal("-600.00"))),"DAILY_LOSS_LIMIT_REACHED"),(context(account_state=account(consecutive_failures=3)),"CONSECUTIVE_FAILURE_LIMIT_REACHED"),(context(account_state=account(open_contracts_total=1)),"POSITION_LIMIT_REACHED")])
def test_directional_plan_fails_closed_on_every_required_safety_gate(risk_context,reason):
    broker=CountingPaperBroker(); result=execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}}),risk_context=risk_context,broker=broker)
    assert result.status=="REJECTED"; assert result.reason_code==reason; assert result.order is None; assert result.fill is None; assert broker.calls==0

@pytest.mark.parametrize("account_state",[account(mode="LIVE"),account(starting_equity_usd=Decimal("100000.00"))])
def test_executor_accepts_only_the_authorized_50k_paper_account(account_state):
    result=execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}}),risk_context=context(account_state=account_state)); assert result.status=="REJECTED"; assert result.reason_code=="UNAUTHORIZED_ACCOUNT"

def test_duplicate_plan_id_cannot_create_a_second_order_or_fill():
    ledger=ExecutionLedger(); broker=CountingPaperBroker(); document=plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}}); first=execute(document,ledger=ledger,broker=broker); second=execute(document,ledger=ledger,broker=broker)
    assert first.status=="FILLED"; assert second==first; assert broker.calls==1
    assert len([r for r in first.receipts if r["stage"]=="PAPER_ORDERED"])==1; assert len([r for r in first.receipts if r["stage"]=="PAPER_FILLED_OR_REJECTED"])==1

def test_reservation_hash_must_match_the_exact_immutable_plan():
    broker=CountingPaperBroker(); result=execute(plan(),broker=broker,reservation_hash="0"*64); assert result.status=="REJECTED"; assert result.reason_code=="RESERVATION_HASH_MISMATCH"; assert broker.calls==0

def test_paper_fill_is_deterministic_conservative_and_uses_next_bar_only():
    document=plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}}); first=execute(document); second=execute(document,ledger=ExecutionLedger())
    assert first.fill==second.fill; assert first.fill.price==Decimal("6000.25"); assert first.fill.occurred_at==NOW; assert first.fill.price!=market().next_bar_open; assert first.fill.order_id==first.order.order_id

def test_entry_fill_keeps_open_position_nonterminal_until_exit_lifecycle_completes():
    result=execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}})); assert result.position is not None; assert result.position.status=="OPEN"; assert result.terminal is False; assert result.reason_code=="PAPER_ENTRY_FILLED_POSITION_OPEN"
    assert [r["stage"] for r in result.receipts]==["EXECUTOR_RECEIVED","RISK_DECIDED","PAPER_ORDERED","PAPER_FILLED_OR_REJECTED"]

def test_receipts_are_deterministic_and_correlated_by_event_and_plan():
    result=execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}})); assert [r["stage"] for r in result.receipts]==["EXECUTOR_RECEIVED","RISK_DECIDED","PAPER_ORDERED","PAPER_FILLED_OR_REJECTED"]
    assert all(r["event_id"]==EVENT_ID for r in result.receipts); assert all(r["plan_id"]==EVENT_ID for r in result.receipts); assert len({r["receipt_id"] for r in result.receipts})==len(result.receipts)

def test_trade_risk_and_quantity_limits_fail_closed():
    assert execute(plan(position_action={"quantity":1,"protective_stop":{"price":"5969.00"},"take_profit":{"price":"6005.00"}})).reason_code=="MAX_TRADE_RISK_EXCEEDED"
    assert execute(plan(position_action={"quantity":2,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}})).reason_code=="POSITION_LIMIT_EXCEEDED"

def test_market_provenance_and_three_closed_bar_start_gate_fail_closed():
    document=plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}})
    assert execute(document,risk_context=context(market_state=market(data_class="TEST"))).reason_code=="UNTRUSTED_FEED"
    assert execute(document,risk_context=context(market_state=market(consecutive_closed_bars=2))).reason_code=="INSUFFICIENT_FEED_HISTORY"

def test_target_exit_closes_open_position_and_emits_true_terminal_completion():
    from trading_core import paper_execution as paper_execution_module
    from trading_core.paper_lifecycle import Bar
    close_open_position=getattr(paper_execution_module,"close_open_position",None); assert close_open_position is not None,"public core must expose deterministic OPEN->CLOSED paper lifecycle"
    document=plan(position_action={"quantity":1,"protective_stop":{"price":"5990.00"},"take_profit":{"price":"6005.00"}}); entry=execute(document); bar=Bar(open=Decimal("6001.00"),high=Decimal("6006.00"),low=Decimal("6000.00"),close=Decimal("6004.00"))
    result=close_open_position(document,entry,bar,occurred_at=NOW+timedelta(minutes=5))
    assert result.terminal is True; assert result.status=="CLOSED"; assert result.reason_code=="TARGET_FILLED"; assert result.position is not None and result.position.status=="CLOSED"; assert result.trade is not None and result.trade.role=="EXIT"; assert result.trade.position_id==entry.position.position_id; assert result.trade.price==Decimal("6005.00")
    assert [receipt["stage"] for receipt in result.receipts][-2:]==["PAPER_EXIT_FILLED","COMPLETED"]; assert all(receipt["event_id"]==EVENT_ID and receipt["plan_id"]==EVENT_ID for receipt in result.receipts)
