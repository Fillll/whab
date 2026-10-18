"""Next-month planning conserves cash while keeping category allocations explicit."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace, select_currency
from whab.domain.errors import StoreError
from whab.domain.money import MAX_MINOR_UNITS, MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget


@pytest.fixture
def state():
    return {
        "budgetStartMonth": "2026-10",
        "accounts": {"cash": {"id": "cash", "type": "depository", "balance": 100000}},
        "categories": {
            "rent": {"id": "rent", "name": "Rent", "group": "Home", "target": 0},
            "food": {"id": "food", "name": "Food", "group": "Everyday", "target": 0},
        },
        "transactions": {}, "budget": {},
    }


def reserve(state, assigned, month="2026-10", currency="USD", as_of="2026-10"):
    commands.assign_next_month(state, month, currency, assigned, as_of_month=as_of)


def test_saved_next_month_pool_releases_into_next_month_without_creating_assignments(state):
    before = deepcopy(state)
    reserve(state, 60000)
    assert state == {**before, "futureFunding": {"2026-11": {"USD": 60000}}}
    october = calculate_workspace(state, "2026-10")
    november = calculate_workspace(state, "2026-11")
    assert october["nextMonthFundingEditable"] is True
    assert october["summary"]["assignedNextMonth"] == 60000
    assert october["summary"]["readyToAssign"] == 40000
    assert november["summary"]["assignedNextMonth"] == 0
    assert november["summary"]["readyToAssign"] == 100000
    assert november["summary"]["assigned"] == 0


def test_future_category_plans_leave_the_explicit_transfer_and_source_pool_unchanged(state):
    reserve(state, 60000)
    commands.assign(state, "rent", "2026-11", 40000, as_of_month="2026-10")
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 40000
    commands.assign(state, "food", "2026-11", 20000, as_of_month="2026-10")
    assert calculate_workspace(state, "2026-10")["summary"]["assignedNextMonth"] == 60000
    commands.assign(state, "food", "2026-11", 60000, as_of_month="2026-10")
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 40000
    assert calculate_workspace(state, "2026-11")["summary"]["readyToAssign"] == 0
    before = deepcopy(state)
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign(state, "rent", "2026-11", 40001, as_of_month="2026-10")
    assert state == before


def test_explicit_transfer_can_be_lowered_below_category_plans_without_erasing_them(state):
    reserve(state, 60000)
    commands.assign(state, "rent", "2026-11", 80000, as_of_month="2026-10")
    reserve(state, 70000)
    assert calculate_workspace(state, "2026-11")["summary"]["assigned"] == 80000
    commands.assign(state, "rent", "2026-11", 20000, as_of_month="2026-10")
    assert calculate_workspace(state, "2026-10")["summary"]["assignedNextMonth"] == 70000
    reserve(state, 20000)
    assert state["budget"] == {"2026-11": {"rent": 20000}}
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 80000


def test_current_assignments_cannot_spend_reserved_cash_and_reductions_remain_allowed(state):
    reserve(state, 60000)
    commands.assign(state, "rent", "2026-10", 40000, as_of_month="2026-10")
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign(state, "food", "2026-10", 1, as_of_month="2026-10")
    state["accounts"]["cash"]["balance"] = 0
    reserve(state, 50000)
    commands.assign(state, "rent", "2026-10", 10000, as_of_month="2026-10")
    # The old floor cannot authorize worsening a future month after cash is lost.
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign(state, "rent", "2026-11", 10000, as_of_month="2026-10")
    with pytest.raises(StoreError, match="exceeds available"):
        reserve(state, 50001)


def test_all_future_targets_are_funded_and_year_rollover_is_exact(state):
    reserve(state, 30000, month="2026-12")
    reserve(state, 40000, month="2027-01")
    assert state["futureFunding"] == {
        "2027-01": {"USD": 30000}, "2027-02": {"USD": 40000},
    }
    assert calculate_workspace(state, "2026-12")["summary"]["readyToAssign"] == 70000
    assert calculate_workspace(state, "2027-01")["summary"]["readyToAssign"] == 60000
    assert calculate_workspace(state, "2027-02")["summary"]["readyToAssign"] == 100000
    with pytest.raises(StoreError, match="exceeds available"):
        reserve(state, 100001, month="2027-01")


def test_last_supported_target_month_can_be_funded(state):
    reserve(state, 100000, month="2200-11")
    assert state["futureFunding"] == {"2200-12": {"USD": 100000}}
    assert calculate_workspace(state, "2200-11")["summary"]["readyToAssign"] == 0
    assert calculate_workspace(state, "2200-12")["summary"]["readyToAssign"] == 100000


def test_negative_legacy_future_assignments_cannot_invent_funding(state):
    state["budget"] = {"2026-11": {"rent": -50000}}
    view = calculate_workspace(state, "2026-10")
    assert view["summary"]["assignedNextMonth"] == 0
    assert view["summary"]["readyToAssign"] == 100000
    with pytest.raises(StoreError, match="exceeds available"):
        reserve(state, 100001)


def test_reserve_currency_is_isolated_and_currencies_without_categories_are_projected(state):
    state["accounts"]["yen"] = {
        "id": "yen", "type": "depository", "balance": 5000, "currency": "JPY",
    }
    reserve(state, 5000, currency="JPY")
    reserve(state, 0, currency="EUR")
    view = calculate_workspace(state, "2026-10")
    assert view["currencies"] == ["EUR", "JPY", "USD"]
    assert view["summary"]["readyToAssign"] == 100000
    assert select_currency(view, "JPY")["summary"]["readyToAssign"] == 0
    assert select_currency(view, "JPY")["summary"]["assignedNextMonth"] == 5000
    assert select_currency(view, "EUR")["summary"]["assignedNextMonth"] == 0
    with pytest.raises(StoreError, match="exceeds available"):
        reserve(state, 1, currency="EUR")


@pytest.mark.parametrize("value", [True, 1.5, "1", MAX_MINOR_UNITS + 1, -1])
def test_invalid_funding_amounts_leave_state_unchanged(state, value):
    before = deepcopy(state)
    with pytest.raises(StoreError):
        reserve(state, value)
    assert state == before


@pytest.mark.parametrize("month", ["2026-09", "2200-12", "2201-01", "bad"])
def test_unavailable_source_months_leave_state_unchanged(state, month):
    before = deepcopy(state)
    with pytest.raises(StoreError):
        reserve(state, 0, month=month)
    assert state == before


def test_future_funding_safe_integer_aggregate_is_bounded(state):
    state["budget"] = {"2026-10": {"rent": -MAX_SAFE_INTEGER}}
    state["futureFunding"] = {"2026-11": {"USD": 1}}
    with pytest.raises(StoreError, match="totals exceed"):
        calculate_workspace(state, "2026-10")


def snapshot(**changes):
    return {
        "formatVersion": 1, "budgetStartMonth": "2026-10",
        "accounts": [], "categories": [], "transactions": [], "assignments": [],
        "rolloverModes": [],
        **changes,
    }


def test_portable_old_snapshots_normalize_and_funding_is_an_independent_copy():
    assert validate_portable_budget(snapshot())["futureFunding"] == []
    row = {"month": "2026-11", "currency": "JPY", "assigned": 123}
    data = snapshot(futureFunding=[row])
    assert validate_portable_budget(data) == data
    restored = validate_portable_budget(data)
    row["assigned"] = 456
    assert restored["futureFunding"][0]["assigned"] == 123


@pytest.mark.parametrize("changes", [
    {"month": "2026-10"}, {"month": "2201-01"}, {"month": "bad"},
    {"currency": "usd"}, {"currency": "ZZZ"}, {"assigned": -1},
    {"assigned": True}, {"assigned": 1.25}, {"assigned": MAX_MINOR_UNITS + 1},
    {"token": "must-not-export"},
])
def test_portable_funding_is_strictly_validated(changes):
    data = snapshot(futureFunding=[{
        "month": "2026-11", "currency": "USD", "assigned": 0, **changes,
    }])
    with pytest.raises(StoreError):
        validate_portable_budget(data)


def test_duplicate_portable_funding_is_rejected():
    row = {"month": "2026-11", "currency": "USD", "assigned": 0}
    with pytest.raises(StoreError, match="Duplicate"):
        validate_portable_budget(snapshot(futureFunding=[row, row.copy()]))
