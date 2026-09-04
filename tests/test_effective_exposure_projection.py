from trading_core.risk_admission import effective_exposure_contracts


def test_reservation_counts_before_execution_is_open():
    assert effective_exposure_contracts(
        open_contracts_by_plan={},
        reserved_contracts_by_plan={"plan-1": 2},
    ) == 2


def test_open_before_release_does_not_double_count_same_plan():
    assert effective_exposure_contracts(
        open_contracts_by_plan={"plan-1": 2},
        reserved_contracts_by_plan={"plan-1": 2},
    ) == 2


def test_released_reservation_leaves_open_exposure_counted():
    assert effective_exposure_contracts(
        open_contracts_by_plan={"plan-1": 2},
        reserved_contracts_by_plan={},
    ) == 2


def test_distinct_open_and_reserved_plans_both_count():
    assert effective_exposure_contracts(
        open_contracts_by_plan={"plan-open": 2},
        reserved_contracts_by_plan={"plan-reserved": 1},
    ) == 3
