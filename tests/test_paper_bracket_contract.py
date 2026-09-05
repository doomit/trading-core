import pytest

from trading_core.paper_bracket import PaperBracketRelationship, apply_oco_fill, build_paper_bracket


def test_public_package_exports_bracket_contract():
    from trading_core import (
        PaperBracketRelationship as ExportedRelationship,
        apply_oco_fill as exported_apply_oco_fill,
        build_paper_bracket as exported_build_paper_bracket,
    )

    assert ExportedRelationship is PaperBracketRelationship
    assert exported_apply_oco_fill is apply_oco_fill
    assert exported_build_paper_bracket is build_paper_bracket


def test_bracket_relationship_has_deterministic_parent_sibling_and_oco_identity():
    first = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)
    second = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)

    assert first == second
    assert first.parent_order_id == "paper-order:entry-123"
    assert first.bracket_id.startswith("paper-bracket:")
    assert first.oco_group_id.startswith("paper-oco:")
    assert first.stop_order_id.startswith("paper-order:stop:")
    assert first.target_order_id.startswith("paper-order:target:")
    assert len({first.stop_order_id, first.target_order_id, first.parent_order_id}) == 3
    assert first.remaining_quantity == 2
    assert first.active_stop_quantity == 2
    assert first.active_target_quantity == 2
    assert first.cancelled_order_ids == ()
    assert first.status == "ACTIVE"


def test_durable_record_round_trip_preserves_explicit_relationship_state():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)
    bracket = apply_oco_fill(bracket, filled_order_id=bracket.target_order_id, filled_quantity=1)

    record = bracket.to_record()

    assert record["schema"] == "paper_bracket_relationship_v1"
    assert record["parent_order_id"] == bracket.parent_order_id
    assert record["bracket_id"] == bracket.bracket_id
    assert record["oco_group_id"] == bracket.oco_group_id
    assert record["stop_order_id"] == bracket.stop_order_id
    assert record["target_order_id"] == bracket.target_order_id
    assert record["original_quantity"] == 2
    assert record["remaining_quantity"] == 1
    assert record["cancelled_order_ids"] == []
    assert record["status"] == "ACTIVE"
    assert PaperBracketRelationship.from_record(record) == bracket


def test_durable_record_rejects_tampered_deterministic_identity():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=1)
    record = bracket.to_record()
    record["bracket_id"] = "paper-bracket:tampered"

    with pytest.raises(ValueError, match="identity"):
        PaperBracketRelationship.from_record(record)


def test_durable_record_rejects_unknown_status():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=1)
    record = bracket.to_record()
    record["status"] = "MAYBE"

    with pytest.raises(ValueError, match="status"):
        PaperBracketRelationship.from_record(record)


def test_durable_record_rejects_active_state_with_zero_remaining_quantity():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=1)
    record = bracket.to_record()
    record["remaining_quantity"] = 0

    with pytest.raises(ValueError, match="ACTIVE"):
        PaperBracketRelationship.from_record(record)


def test_durable_record_rejects_closed_state_with_remaining_quantity():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)
    record = bracket.to_record()
    record["status"] = "CLOSED"
    record["remaining_quantity"] = 1
    record["cancelled_order_ids"] = [bracket.stop_order_id]

    with pytest.raises(ValueError, match="CLOSED"):
        PaperBracketRelationship.from_record(record)


def test_durable_record_rejects_unknown_cancelled_order_identity():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=1)
    record = bracket.to_record()
    record["cancelled_order_ids"] = ["paper-order:unrelated"]

    with pytest.raises(ValueError, match="cancelled"):
        PaperBracketRelationship.from_record(record)


def test_partial_target_fill_keeps_remaining_stop_protection_active():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)

    updated = apply_oco_fill(bracket, filled_order_id=bracket.target_order_id, filled_quantity=1)

    assert updated.remaining_quantity == 1
    assert updated.active_stop_quantity == 1
    assert updated.active_target_quantity == 1
    assert updated.cancelled_order_ids == ()
    assert updated.status == "ACTIVE"


def test_one_shot_partial_target_becomes_inactive_while_stop_remains_active_after_round_trip():
    bracket = build_paper_bracket(
        parent_order_id="paper-order:entry-123",
        quantity=2,
        target_quantity=1,
    )

    updated = apply_oco_fill(bracket, filled_order_id=bracket.target_order_id, filled_quantity=1)

    assert updated.remaining_quantity == 1
    assert updated.active_stop_quantity == 1
    assert updated.active_target_quantity == 0
    assert updated.cancelled_order_ids == ()
    assert updated.filled_order_ids == (bracket.target_order_id,)
    assert updated.status == "ACTIVE"

    restored = PaperBracketRelationship.from_record(updated.to_record())
    assert restored == updated
    assert restored.active_stop_quantity == 1
    assert restored.active_target_quantity == 0
    assert restored.cancelled_order_ids == ()
    assert restored.filled_order_ids == (bracket.target_order_id,)


def test_full_target_fill_closes_bracket_and_cancels_stop_sibling():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)

    updated = apply_oco_fill(bracket, filled_order_id=bracket.target_order_id, filled_quantity=2)

    assert updated.remaining_quantity == 0
    assert updated.active_stop_quantity == 0
    assert updated.active_target_quantity == 0
    assert updated.cancelled_order_ids == (bracket.stop_order_id,)
    assert updated.status == "CLOSED"


def test_full_stop_fill_closes_bracket_and_cancels_target_sibling():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=1)

    updated = apply_oco_fill(bracket, filled_order_id=bracket.stop_order_id, filled_quantity=1)

    assert updated.remaining_quantity == 0
    assert updated.cancelled_order_ids == (bracket.target_order_id,)
    assert updated.status == "CLOSED"
