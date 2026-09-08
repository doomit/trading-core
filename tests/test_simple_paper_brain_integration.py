from __future__ import annotations

from datetime import datetime, timezone

from trading_core.simple_paper_brain_integration import (
    brain_run_path,
    market_path,
    plan_path,
    position_path,
    run_scheduled_brain_job,
)
from trading_core.simple_paper_position_execution import new_flat_position


NOW = datetime(2026, 9, 8, 3, 15, tzinfo=timezone.utc)


def _market(symbol: str) -> dict:
    return {
        "schema": "market_snapshot_v1",
        "symbol": symbol,
        "updated_at": "2026-09-08T03:15:01Z",
        "latest_bar_end": "2026-09-08T03:15:00Z",
        "db_committed_at": "2026-09-08T03:15:01Z",
        "github_mirrored_at": "2026-09-08T03:15:01Z",
        "bars": [
            {
                "start": "2026-09-08T03:14:00Z",
                "end": "2026-09-08T03:15:00Z",
                "open": 6500.0,
                "high": 6502.0,
                "low": 6499.0,
                "close": 6501.0,
                "volume": 100.0,
            }
        ],
    }


def _rules() -> dict:
    return {
        "schema": "execution_rules_v1",
        "updated_at": "2026-09-07T16:00:00Z",
        "mode": "PAPER",
        "symbols": ["MES", "MNQ"],
        "max_contracts_per_symbol": 6,
        "one_active_position_per_symbol": True,
        "paper_initial_cash_usd": 10000000,
        "eod": {"timezone": "America/Los_Angeles", "force_close_time": "15:00:00"},
        "stale_feed_forced_stop_minutes": 15,
        "same_bar_exit_policy": "ADVERSE_FIRST",
    }


def _no_trade(symbol: str) -> dict:
    return {
        "schema": "trading_plan_v2",
        "plan_id": f"plan-{symbol.lower()}-0315",
        "symbol": symbol,
        "analysis_bar_end": "2026-09-08T03:15:00Z",
        "generated_at": "2026-09-08T03:15:02Z",
        "action_valid_until": "2026-09-08T03:29:59Z",
        "target_position": {"state": "FLAT", "position_id": None, "position_version": None},
        "decision": "NO_TRADE",
        "side": None,
        "confidence": 0.55,
        "analysis_summary": ["No clean setup."],
        "entry": None,
        "protection": None,
        "add_once": None,
        "reduce_once": None,
    }


def _open(symbol: str, qty: int = 2) -> dict:
    plan = _no_trade(symbol)
    plan.update(
        {
            "plan_id": f"plan-{symbol.lower()}-open",
            "decision": "OPEN",
            "side": "LONG",
            "entry": {"order_type": "MARKET", "trigger_price": None, "qty": qty},
            "protection": {"stop_loss": 6490.0, "take_profit": 6520.0},
        }
    )
    return plan


def _runtime_inputs(*, missing_position: str | None = None, invalid_previous: str | None = None):
    docs: dict[str, dict] = {}
    for symbol in ("MES", "MNQ"):
        docs[market_path(symbol)] = _market(symbol)
        if symbol != missing_position:
            docs[position_path(symbol)] = new_flat_position(symbol, NOW)
        if symbol == invalid_previous:
            docs[plan_path(symbol)] = {"schema": "trading_plan_v2", "broken": True}
    return docs


def _run(runtime_docs, generator, publisher):
    return run_scheduled_brain_job(
        run_id="run-task05-test",
        scheduled_run_time=NOW,
        generated_at=datetime(2026, 9, 8, 3, 15, 2, tzinfo=timezone.utc),
        read_runtime_json=lambda path: runtime_docs.get(path),
        read_control_json=lambda path: _rules(),
        read_control_text=lambda path: "strategy prompt",
        generate_plan=generator,
        publish_runtime_json=publisher,
    )


def test_exact_contract_paths_are_stable():
    assert market_path("MES") == "runtime/simple-paper/market/MES/current.json"
    assert position_path("MNQ") == "runtime/simple-paper/position/MNQ/current.json"
    assert plan_path("MES") == "runtime/simple-paper/plan/MES/current.json"
    assert brain_run_path("abc") == "runtime/simple-paper/brain-runs/abc.json"


def test_valid_symbol_plans_publish_only_after_validation():
    runtime_docs = _runtime_inputs()
    writes: list[tuple[str, dict]] = []

    result = _run(
        runtime_docs,
        generator=lambda symbol, **_: _no_trade(symbol),
        publisher=lambda path, doc: writes.append((path, doc)),
    )

    assert result["result"] == "published"
    assert [path for path, _ in writes] == [plan_path("MES"), plan_path("MNQ")]
    assert result["symbols"]["MES"]["output_validation"] == "passed"
    assert result["symbols"]["MES"]["published_plan"]["plan_id"] == "plan-mes-0315"


def test_missing_required_position_blocks_only_that_symbol():
    runtime_docs = _runtime_inputs(missing_position="MES")
    generated: list[str] = []
    writes: list[str] = []

    result = _run(
        runtime_docs,
        generator=lambda symbol, **_: generated.append(symbol) or _no_trade(symbol),
        publisher=lambda path, doc: writes.append(path),
    )

    assert generated == ["MNQ"]
    assert writes == [plan_path("MNQ")]
    assert result["symbols"]["MES"]["published_plan"] is None
    assert "position" in result["symbols"]["MES"]["blocker"].lower()
    assert result["symbols"]["MNQ"]["published_plan"]["plan_id"] == "plan-mnq-0315"


def test_invalid_generated_plan_never_replaces_previous_plan():
    runtime_docs = _runtime_inputs()
    writes: list[str] = []

    def generator(symbol, **_):
        plan = _no_trade(symbol)
        if symbol == "MES":
            plan["target_position"] = {"state": "OPEN", "position_id": "wrong", "position_version": 9}
        return plan

    result = _run(runtime_docs, generator=generator, publisher=lambda path, doc: writes.append(path))

    assert writes == [plan_path("MNQ")]
    assert result["symbols"]["MES"]["output_validation"] == "failed"
    assert result["symbols"]["MES"]["published_plan"] is None


def test_quantity_limit_is_enforced_before_publish():
    runtime_docs = _runtime_inputs()
    writes: list[str] = []

    result = _run(
        runtime_docs,
        generator=lambda symbol, **_: _open(symbol, qty=6) | {
            "add_once": {"order_type": "MARKET", "trigger_price": None, "qty": 1}
        },
        publisher=lambda path, doc: writes.append(path),
    )

    assert writes == []
    assert result["symbols"]["MES"]["output_validation"] == "failed"
    assert "quantity" in result["symbols"]["MES"]["blocker"].lower()


def test_invalid_previous_plan_is_an_input_blocker_for_only_that_symbol():
    runtime_docs = _runtime_inputs(invalid_previous="MES")
    generated: list[str] = []
    writes: list[str] = []

    result = _run(
        runtime_docs,
        generator=lambda symbol, **_: generated.append(symbol) or _no_trade(symbol),
        publisher=lambda path, doc: writes.append(path),
    )

    assert generated == ["MNQ"]
    assert writes == [plan_path("MNQ")]
    assert "previous plan" in result["symbols"]["MES"]["blocker"].lower()


def test_publish_failure_is_recorded_and_not_reported_as_published():
    runtime_docs = _runtime_inputs()

    def publisher(path, doc):
        if path == plan_path("MES"):
            raise RuntimeError("github write failed")

    result = _run(runtime_docs, generator=lambda symbol, **_: _no_trade(symbol), publisher=publisher)

    assert result["symbols"]["MES"]["output_validation"] == "passed"
    assert result["symbols"]["MES"]["published_plan"] is None
    assert "write failed" in result["symbols"]["MES"]["blocker"].lower()
    assert result["symbols"]["MNQ"]["published_plan"]["plan_id"] == "plan-mnq-0315"
