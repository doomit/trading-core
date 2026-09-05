from trading_core.paper_bracket import apply_oco_fill, build_paper_bracket


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


def test_partial_target_fill_keeps_remaining_stop_protection_active():
    bracket = build_paper_bracket(parent_order_id="paper-order:entry-123", quantity=2)

    updated = apply_oco_fill(bracket, filled_order_id=bracket.target_order_id, filled_quantity=1)

    assert updated.remaining_quantity == 1
    assert updated.active_stop_quantity == 1
    assert updated.active_target_quantity == 1
    assert updated.cancelled_order_ids == ()
    assert updated.status == "ACTIVE"


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
