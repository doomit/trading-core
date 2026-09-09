import json
from importlib.resources import files
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


def test_canonical_execution_rules_file_validates_against_public_contract():
    repo_root = Path(__file__).resolve().parents[1]
    rules_path = repo_root / "config" / "simple-paper" / "execution-rules-v1.json"
    value = json.loads(rules_path.read_text(encoding="utf-8"))
    schema = json.loads(
        (files("trading_core.schemas") / "execution_rules_v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(value)

    assert value["mode"] == "PAPER"
    assert value["symbols"] == ["MES", "MNQ"]
    assert value["max_contracts_per_symbol"] == 6
    assert value["paper_initial_cash_usd"] == 10_000_000
    assert value["eod"] == {
        "timezone": "America/Los_Angeles",
        "force_close_time": "23:55:00",
    }
    assert value["stale_feed_forced_stop_minutes"] == 15
    assert value["same_bar_exit_policy"] == "ADVERSE_FIRST"
