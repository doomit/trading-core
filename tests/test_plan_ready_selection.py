from trading_core.plan_ready_selection import fresh_first_bounded_candidates


def test_fresh_plan_ready_candidate_is_not_starved_by_stale_fallback_batch():
    stale = [
        {"event_id": f"evt_stale_{i:02d}", "plan_id": f"evt_stale_{i:02d}"}
        for i in range(20)
    ]
    target = {"event_id": "evt_fresh_target", "plan_id": "evt_fresh_target"}

    selected = list(
        fresh_first_bounded_candidates(
            fresh_entities=[target],
            fallback_entities=stale + [target],
            batch_size=20,
        )
    )

    assert len(selected) == 20
    assert selected[0]["event_id"] == "evt_fresh_target"
    assert sum(row["event_id"] == "evt_fresh_target" for row in selected) == 1
