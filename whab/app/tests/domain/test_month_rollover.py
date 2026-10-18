"""Month-boundary policy pools category balances without changing account money."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace, funding_capacity, select_currency
from whab.domain.errors import StoreError
from whab.domain.money import MAX_SAFE_INTEGER
from whab.domain.portable import validate_portable_budget


def transaction(identifier, month, amount, category="food", account="cash"):
    return {
        "id": identifier, "accountId": account, "date": f"{month}-10",
        "merchant": "Fixture", "amount": amount, "categoryId": category,
        "pending": False, "reviewed": False, "source": "manual", "note": "",
        "isTransfer": False, "categoryEdited": False,
    }


def rollover(state, month, currency, mode, *, as_of="2026-10"):
    commands.set_rollover_mode(state, month, currency, mode, as_of_month=as_of)

@pytest.fixture
def state():
    return {
        "budgetStartMonth": "2026-01",
        "accounts": {"cash": {"id": "cash", "type": "depository", "balance": 12500}},
        "categories": {
            "food": {"id": "food", "name": "Food", "group": "Everyday", "target": 0},
            "rent": {"id": "rent", "name": "Rent", "group": "Home", "target": 0},
        },
        "budget": {"2026-01": {"food": 10000, "rent": 5000}},
        "transactions": {
            "food": transaction("food", "2026-01", 5000),
            "rent": transaction("rent", "2026-01", 7500, "rent"),
        },
    }


def balances(view):
    return {row["categoryId"]: (row["carryover"], row["available"]) for row in view["budget"]}


def test_default_category_mode_keeps_both_signs_in_their_own_categories(state):
    before = deepcopy(state)
    february = calculate_workspace(state, "2026-02")
    assert february["rolloverMode"] == "category"
    assert february["rolloverModeEditable"] is True
    assert balances(february) == {"food": (5000, 5000), "rent": (-2500, -2500)}
    assert february["summary"]["previousOverspending"] == 2500
    assert february["summary"]["balanceCarryover"] == 0
    assert february["summary"]["readyToAssign"] == 7500
    assert state == before


def test_balance_mode_pools_net_signed_categories_without_adding_cash_twice(state):
    before = deepcopy(state)
    rollover(state, "2026-01", "USD", "balance")
    assert state == {**before, "rolloverModes": {"2026-01": {"USD": "balance"}}}
    january = calculate_workspace(state, "2026-01")
    february = calculate_workspace(state, "2026-02")
    assert january["rolloverMode"] == "balance"
    assert balances(february) == {"food": (0, 0), "rent": (0, 0)}
    assert february["summary"]["balanceCarryover"] == 2500
    assert february["summary"]["previousOverspending"] == 0
    assert february["summary"]["readyToAssign"] == 12500
    assert january["summary"] == calculate_workspace(before, "2026-01")["summary"]
    # The explanatory incoming line appears once; its cash remains available.
    march = calculate_workspace(state, "2026-03")
    assert march["summary"]["balanceCarryover"] == 0
    assert march["summary"]["readyToAssign"] == 12500


def test_negative_pooled_balance_is_already_reflected_in_cash(state):
    state["transactions"]["food"]["amount"] = 10000
    state["accounts"]["cash"]["balance"] = 7500
    rollover(state, "2026-01", "USD", "balance")
    february = calculate_workspace(state, "2026-02")
    assert february["summary"]["balanceCarryover"] == -2500
    assert february["summary"]["available"] == 0
    assert february["summary"]["readyToAssign"] == 7500


def test_mixed_boundaries_and_empty_months_apply_each_source_rule_once(state):
    state["budget"]["2026-02"] = {"food": 2000, "rent": 3000}
    state["budget"]["2026-03"] = {"food": 4000}
    state["transactions"].update({
        "feb-food": transaction("feb-food", "2026-02", 1000),
        "feb-rent": transaction("feb-rent", "2026-02", 1000, "rent"),
        "mar-food": transaction("mar-food", "2026-03", 500),
        "mar-rent": transaction("mar-rent", "2026-03", 500, "rent"),
    })
    rollover(state, "2026-01", "USD", "balance")
    rollover(state, "2026-02", "USD", "category")
    rollover(state, "2026-03", "USD", "balance")
    assert balances(calculate_workspace(state, "2026-02")) == {
        "food": (0, 1000), "rent": (0, 2000),
    }
    assert balances(calculate_workspace(state, "2026-03")) == {
        "food": (1000, 4500), "rent": (2000, 1500),
    }
    april = calculate_workspace(state, "2026-04")
    assert april["summary"]["balanceCarryover"] == 6000
    assert balances(april) == {"food": (0, 0), "rent": (0, 0)}
    assert calculate_workspace(state, "2026-05")["summary"]["balanceCarryover"] == 0
    assert balances(calculate_workspace(state, "2026-05")) == {"food": (0, 0), "rent": (0, 0)}


def test_retroactive_mode_changes_affect_only_later_envelope_projections(state):
    january = calculate_workspace(state, "2026-01")
    category_february = calculate_workspace(state, "2026-02")
    rollover(state, "2026-01", "USD", "balance")
    assert calculate_workspace(state, "2026-01")["summary"] == january["summary"]
    assert calculate_workspace(state, "2026-02")["budget"] != category_february["budget"]
    rollover(state, "2026-01", "USD", "category")
    assert calculate_workspace(state, "2026-02")["budget"] == category_february["budget"]


def test_modes_and_pooled_balances_are_currency_scoped_including_mode_only_currency(state):
    state["accounts"]["yen"] = {
        "id": "yen", "type": "depository", "balance": 9000, "currency": "JPY",
    }
    state["categories"]["yen-food"] = {
        "id": "yen-food", "name": "Food", "group": "Everyday", "target": 0, "currency": "JPY",
    }
    state["budget"]["2026-01"]["yen-food"] = 1000
    rollover(state, "2026-01", "JPY", "balance")
    rollover(state, "2026-02", "EUR", "balance")
    view = calculate_workspace(state, "2026-02")
    assert view["currencies"] == ["EUR", "JPY", "USD"]
    assert view["summary"]["balanceCarryover"] == 0
    assert view["summary"]["previousOverspending"] == 2500
    assert select_currency(view, "JPY")["summary"]["balanceCarryover"] == 1000
    assert select_currency(view, "JPY")["summary"]["readyToAssign"] == 9000
    assert select_currency(view, "EUR")["rolloverMode"] == "balance"
    assert select_currency(view, "USD")["rolloverMode"] == "category"


@pytest.mark.parametrize("month,currency,mode", [
    ("2025-12", "USD", "balance"), ("2200-12", "USD", "balance"),
    ("bad", "USD", "balance"), ("2026-01", "usd", "balance"),
    ("2026-01", "USD", "none"), ("2026-01", "USD", []),
    ("2026-01", "USD", False),
])
def test_invalid_boundary_choices_leave_state_unchanged(state, month, currency, mode):
    before = deepcopy(state)
    with pytest.raises(StoreError):
        rollover(state, month, currency, mode)
    assert state == before


@pytest.mark.parametrize("value", [None, [], {"2026-01": []}, {"2026-01": {"USD": []}}])
def test_malformed_raw_mode_records_are_domain_errors(state, value):
    state["rolloverModes"] = value
    with pytest.raises(StoreError):
        calculate_workspace(state, "2026-02")


def test_first_and_last_supported_source_boundaries_and_unsafe_pool_totals(state):
    rollover(state, "2200-11", "USD", "balance")
    assert calculate_workspace(state, "2200-12")["summary"]["balanceCarryover"] == 2500
    rollover(state, "2026-01", "USD", "balance")
    state["budget"]["2026-01"] = {"food": MAX_SAFE_INTEGER, "rent": 100000}
    with pytest.raises(StoreError, match="totals exceed"):
        calculate_workspace(state, "2026-02")


@pytest.fixture
def six_month_state():
    months = [f"2026-{number:02}" for number in range(5, 11)]
    return {
        "budgetStartMonth": "2026-05",
        "accounts": {"cash": {"id": "cash", "type": "depository", "balance": 3160000}},
        "categories": {"food": {"id": "food", "name": "Food", "group": "Living", "target": 0}},
        "budget": {month: {"food": 550000} for month in months},
        "transactions": {
            row["id"]: row for month in months for row in (
                transaction(f"income-{month}", month, -820000, None),
                transaction(f"spend-{month}", month, 450000),
            )
        },
    }


def test_historical_cash_does_not_deduct_assignments_funded_by_later_income(six_month_state):
    may = calculate_workspace(six_month_state, "2026-05", as_of_month="2026-10")
    june = calculate_workspace(six_month_state, "2026-06", as_of_month="2026-10")
    october = calculate_workspace(six_month_state, "2026-10", as_of_month="2026-10")
    assert may["summary"]["readyToAssign"] == 1210000
    assert june["summary"]["readyToAssign"] == 1480000
    assert october["summary"]["readyToAssign"] == 2560000
    assert funding_capacity(six_month_state, "2026-05", "USD") == -1540000


def test_history_deducts_only_its_own_explicit_next_month_instruction(six_month_state):
    six_month_state["futureFunding"] = {
        "2026-06": {"USD": 600000}, "2026-07": {"USD": 700000},
        "2026-11": {"USD": 100000},
    }
    may = calculate_workspace(six_month_state, "2026-05", as_of_month="2026-10")
    june = calculate_workspace(six_month_state, "2026-06", as_of_month="2026-10")
    october = calculate_workspace(six_month_state, "2026-10", as_of_month="2026-10")
    assert may["summary"]["readyToAssign"] == 610000
    assert june["summary"]["readyToAssign"] == 780000
    assert october["summary"]["readyToAssign"] == 2460000


def test_affordable_historical_assignment_uses_history_and_current_cash(six_month_state):
    commands.assign(six_month_state, "food", "2026-05", 551000, as_of_month="2026-10")
    assert calculate_workspace(six_month_state, "2026-05", as_of_month="2026-10")[
        "summary"
    ]["readyToAssign"] == 1209000
    assert funding_capacity(six_month_state, "2026-10", "USD") == 2559000


def test_historical_edit_cannot_use_later_income_or_consume_current_future_reservations(six_month_state):
    before = deepcopy(six_month_state)
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign(six_month_state, "food", "2026-05", 1760001, as_of_month="2026-10")
    assert six_month_state == before
    six_month_state["budget"]["2026-11"] = {"food": 2559000}
    before = deepcopy(six_month_state)
    with pytest.raises(StoreError, match="current month"):
        commands.assign(six_month_state, "food", "2026-05", 552000, as_of_month="2026-10")
    assert six_month_state == before


def test_historical_edit_does_not_worsen_a_current_deficit_but_a_pool_reset_absorbs_cost(six_month_state):
    six_month_state["budget"]["2026-11"] = {"food": 2600000}
    assert funding_capacity(six_month_state, "2026-10", "USD") == -40000
    with pytest.raises(StoreError, match="current month"):
        commands.assign(six_month_state, "food", "2026-05", 550001, as_of_month="2026-10")
    rollover(six_month_state, "2026-05", "USD", "balance")
    six_month_state["budget"]["2026-11"] = {"food": 2700000}
    assert funding_capacity(six_month_state, "2026-10", "USD") == -40000
    commands.assign(six_month_state, "food", "2026-05", 551000, as_of_month="2026-10")
    assert funding_capacity(six_month_state, "2026-10", "USD") == -40000


def test_past_reserve_uses_historical_headroom_without_creating_cash_today(six_month_state):
    before = funding_capacity(six_month_state, "2026-10", "USD")
    commands.assign_next_month(
        six_month_state, "2026-09", "USD", 1000000, as_of_month="2026-10",
    )
    assert funding_capacity(six_month_state, "2026-10", "USD") == before
    assert calculate_workspace(six_month_state, "2026-09", as_of_month="2026-10")[
        "summary"
    ]["readyToAssign"] == 1290000
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign(six_month_state, "food", "2026-10", 550000 + before + 1, as_of_month="2026-10")
    saved = deepcopy(six_month_state)
    with pytest.raises(StoreError, match="exceeds available"):
        commands.assign_next_month(
            six_month_state, "2026-05", "USD", 1210001, as_of_month="2026-10",
        )
    assert six_month_state == saved


def protected_state():
    return {
        "budgetStartMonth": "2026-10",
        "accounts": {"cash": {"id": "cash", "type": "depository", "balance": 10000}},
        "categories": {
            key: {"id": key, "name": key, "group": "Living", "target": 0}
            for key in ("rent", "food")
        },
        "budget": {"2026-10": {"rent": 10000}, "2026-11": {"food": 10000}},
        "transactions": {},
        "rolloverModes": {"2026-10": {"USD": "balance"}},
    }


def test_restoring_category_carryover_cannot_resurrect_reallocated_money_today():
    state = protected_state()
    assert funding_capacity(state, "2026-11", "USD") == 0
    before = deepcopy(state)
    with pytest.raises(StoreError, match="current month"):
        rollover(state, "2026-10", "USD", "category", as_of="2026-11")
    assert state == before


@pytest.mark.parametrize("change", ["assignment", "rollover"])
def test_later_balance_boundary_cannot_hide_an_intermediate_historical_deficit(change):
    state = protected_state()
    state["rolloverModes"]["2026-11"] = {"USD": "balance"}
    if change == "assignment":
        state["budget"]["2026-10"] = {}
        state["rolloverModes"].pop("2026-10")
    assert funding_capacity(state, "2026-12", "USD") == 10000
    before = deepcopy(state)
    with pytest.raises(StoreError, match="2026-11"):
        if change == "assignment":
            commands.assign(state, "rent", "2026-10", 10000, as_of_month="2026-12")
        else:
            rollover(state, "2026-10", "USD", "category", as_of="2026-12")
    assert state == before


def test_future_mode_changes_preserve_future_cash_after_known_spending():
    state = protected_state()
    state["budgetStartMonth"] = "2026-09"
    state["transactions"]["expense"] = transaction("expense", "2026-11", 10000, None)
    assert funding_capacity(state, "2026-09", "USD") == 0
    before = deepcopy(state)
    with pytest.raises(StoreError, match="2026-11"):
        rollover(state, "2026-10", "USD", "category", as_of="2026-09")
    assert state == before


def test_nonworsening_mode_changes_remain_available_with_an_existing_deficit():
    state = protected_state()
    state["rolloverModes"] = {}
    assert funding_capacity(state, "2026-11", "USD") == -10000
    rollover(state, "2026-10", "USD", "category", as_of="2026-11")
    rollover(state, "2026-10", "USD", "balance", as_of="2026-11")
    assert funding_capacity(state, "2026-11", "USD") == 0


@pytest.mark.parametrize("month", ["2026-10", "2026-11"])
def test_current_and_future_assignments_preserve_cash_after_known_future_spending(month):
    state = protected_state()
    state["budget"] = {}
    state["rolloverModes"] = {}
    state["transactions"]["expense"] = transaction("expense", "2026-11", 10000, None)
    assert funding_capacity(state, "2026-10", "USD") == 20000
    assert funding_capacity(state, "2026-11", "USD") == 10000
    before = deepcopy(state)
    with pytest.raises(StoreError, match="2026-11"):
        commands.assign(state, "rent", month, 15000, as_of_month="2026-10")
    assert state == before


def test_combined_future_assignments_cannot_double_use_cash_needed_for_known_spending():
    state = protected_state()
    state["budgetStartMonth"] = "2026-09"
    state["budget"] = {}
    state["rolloverModes"] = {}
    state["transactions"]["expense"] = transaction("expense", "2026-11", 10000, None)
    commands.assign(state, "rent", "2026-10", 10000, as_of_month="2026-09")
    before = deepcopy(state)
    with pytest.raises(StoreError, match="2026-11"):
        commands.assign(state, "food", "2026-11", 10000, as_of_month="2026-09")
    assert state == before


def test_future_funding_instruction_preserves_its_source_month_after_known_spending():
    state = protected_state()
    state["budget"] = {}
    state["rolloverModes"] = {}
    state["transactions"]["expense"] = transaction("expense", "2026-11", 10000, None)
    before = deepcopy(state)
    with pytest.raises(StoreError, match="2026-11"):
        commands.assign_next_month(state, "2026-11", "USD", 15000, as_of_month="2026-10")
    assert state == before


def test_final_supported_month_category_assignment_does_not_project_beyond_public_range():
    state = protected_state()
    state["budget"] = {}
    state["rolloverModes"] = {}
    commands.assign(state, "rent", "2200-12", 10000, as_of_month="2200-12")
    assert funding_capacity(state, "2200-12", "USD") == 0
    assert state["budget"] == {"2200-12": {"rent": 10000}}


def snapshot(**changes):
    return {
        "formatVersion": 1, "budgetStartMonth": "2026-01",
        "accounts": [], "categories": [], "transactions": [], "assignments": [],
        "futureFunding": [], **changes,
    }


def test_portable_mode_legacy_default_and_independent_copies():
    assert validate_portable_budget(snapshot())["rolloverModes"] == []
    row = {"month": "2026-01", "currency": "JPY", "mode": "balance"}
    data = snapshot(rolloverModes=[row])
    restored = validate_portable_budget(data)
    assert restored == data
    row["mode"] = "category"
    assert restored["rolloverModes"][0]["mode"] == "balance"


@pytest.mark.parametrize("changes", [
    {"month": "2025-12"}, {"month": "2200-12"}, {"month": "bad"},
    {"currency": "usd"}, {"currency": "ZZZ"}, {"mode": "none"},
    {"mode": False}, {"mode": []}, {"token": "must-not-export"},
])
def test_portable_boundary_policy_records_are_strict(changes):
    data = snapshot(rolloverModes=[{
        "month": "2026-01", "currency": "USD", "mode": "category", **changes,
    }])
    with pytest.raises(StoreError):
        validate_portable_budget(data)


def test_portable_duplicate_boundary_policy_is_rejected():
    row = {"month": "2026-01", "currency": "USD", "mode": "balance"}
    with pytest.raises(StoreError, match="Duplicate"):
        validate_portable_budget(snapshot(rolloverModes=[row, row.copy()]))
