"""Monthly allocation identities and conservative cash protection are separate."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace, funding_capacity, select_currency
from whab.domain.errors import StoreError
from whab.domain.money import MAX_MINOR_UNITS


def state(*, opening=100000, start="2026-01", currency="USD"):
    return {
        "budgetStartMonth": start,
        "accounts": {
            "cash": {"id": "cash", "type": "depository", "balance": opening, "currency": currency, "source": "manual"},
        },
        "categories": {
            name: {"id": name, "name": name, "group": "Everyday", "target": 0, "currency": currency}
            for name in ("food", "rent")
        },
        "budget": {}, "transactions": {},
    }


def post(ledger, identifier, month, amount, category="food", *, account="cash", **changes):
    commands.add_transaction(ledger, identifier, {
        "accountId": account, "date": f"{month}-10", "merchant": "Fixture",
        "amount": amount, "categoryId": category, **changes,
    })


def project(ledger, month, currency="USD"):
    result = select_currency(calculate_workspace(ledger, month), currency)
    summary = result["summary"]
    assert result["monthlyFundingFlow"] is True
    assert summary["readyToAssign"] == (
        summary["income"] + summary["carriedIn"]
        - summary["assignedNextMonth"] - summary["assigned"]
    )
    assert summary["carriedIn"] == (
        summary["carriedInUnassigned"] + summary["carriedInSetAside"] + summary["balanceCarryover"]
    )
    assert summary["fundingCapacity"] <= summary["readyToAssign"]
    return result


def assign(ledger, category, month, amount):
    commands.assign(ledger, category, month, amount, as_of_month="2026-01")


def reserve(ledger, month, amount, currency="USD"):
    commands.assign_next_month(ledger, month, currency, amount, as_of_month="2026-01")


def mode(ledger, month, choice):
    commands.set_rollover_mode(ledger, month, "USD", choice, as_of_month="2026-01")


@pytest.mark.parametrize("choice", ["category", "balance"])
def test_income_opening_cash_unassigned_and_explicit_funding_reconcile_once(choice):
    ledger = state()
    post(ledger, "income", "2026-01", -20000, None)
    post(ledger, "purchase", "2026-01", 10000)
    assign(ledger, "food", "2026-01", 30000)
    reserve(ledger, "2026-01", 20000)
    mode(ledger, "2026-01", choice)
    january = project(ledger, "2026-01")["summary"]
    february = project(ledger, "2026-02")
    assert january["income"] == 20000
    assert january["carriedIn"] == january["carriedInUnassigned"] == 100000
    assert january["carriedInSetAside"] == january["balanceCarryover"] == 0
    assert january["readyToAssign"] == 70000
    assert february["summary"]["carriedInUnassigned"] == 70000
    assert february["summary"]["carriedInSetAside"] == 20000
    pool = 20000 if choice == "balance" else 0
    assert february["summary"]["balanceCarryover"] == pool
    assert february["summary"]["carriedIn"] == 90000 + pool
    assert february["budget"][0]["carryover"] == 20000 - pool
    for view in (project(ledger, "2026-01"), february):
        total = view["summary"]
        assert total["readyToAssign"] + total["assignedNextMonth"] + total["available"] == 110000


def test_reserving_money_changes_components_not_next_month_total_or_category_plans():
    ledger = state()
    assign(ledger, "rent", "2026-02", 80000)
    before = project(ledger, "2026-02")["summary"]
    assert project(ledger, "2026-01")["summary"]["readyToAssign"] == 100000
    reserve(ledger, "2026-01", 60000)
    after = project(ledger, "2026-02")["summary"]
    assert (before["carriedIn"], after["carriedIn"]) == (100000, 100000)
    assert (after["carriedInUnassigned"], after["carriedInSetAside"]) == (40000, 60000)
    reserve(ledger, "2026-01", 10000)
    assert ledger["budget"] == {"2026-02": {"rent": 80000}}
    assert project(ledger, "2026-01")["summary"]["readyToAssign"] == 90000
    assert project(ledger, "2026-02")["summary"]["readyToAssign"] == 20000
    reserve(ledger, "2026-01", 0)
    assert project(ledger, "2026-02")["summary"]["carriedInSetAside"] == 0


def test_released_reservation_can_fund_next_month_even_with_no_current_capacity():
    ledger = state(opening=10000)
    reserve(ledger, "2026-01", 10000)
    assert funding_capacity(ledger, "2026-01", "USD") == 0
    assign(ledger, "food", "2026-02", 10000)
    assert project(ledger, "2026-02")["summary"]["readyToAssign"] == 0


def test_signed_category_carry_is_excluded_from_top_and_does_not_offset_safe_cash():
    ledger = state(opening=20000)
    ledger["budget"] = {"2026-01": {"food": 10000, "rent": 5000}}
    post(ledger, "rent", "2026-01", 8000, "rent")
    february = project(ledger, "2026-02")
    assert [row["carryover"] for row in february["budget"]] == [10000, -3000]
    assert february["summary"]["carriedIn"] == 5000
    assert february["summary"]["balanceCarryover"] == 0
    assert february["summary"]["fundingCapacity"] == 2000
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match="exceeds available"):
        assign(ledger, "food", "2026-02", 2001)
    assert ledger == before
    mode(ledger, "2026-01", "balance")
    pooled = project(ledger, "2026-02")["summary"]
    assert pooled["balanceCarryover"] == 7000
    assert pooled["carriedInUnassigned"] == 5000
    assert pooled["carriedIn"] == pooled["fundingCapacity"] == 12000
    mode(ledger, "2026-01", "category")
    assert project(ledger, "2026-02") == february


def test_negative_overall_balance_is_included_once_and_not_lost():
    ledger = state(opening=20000)
    ledger["budget"] = {"2026-01": {"food": 10000}}
    post(ledger, "expense", "2026-01", 15000)
    mode(ledger, "2026-01", "balance")
    february = project(ledger, "2026-02")["summary"]
    assert february["balanceCarryover"] == -5000
    assert february["carriedInUnassigned"] == 10000
    assert february["carriedIn"] == february["readyToAssign"] == 5000
    assert project(ledger, "2026-03")["summary"]["carriedIn"] == 5000
    assert project(ledger, "2026-03")["summary"]["balanceCarryover"] == 0


def test_chained_funding_releases_once_across_year_and_empty_months():
    ledger = state(start="2026-12")
    reserve(ledger, "2026-12", 60000)
    reserve(ledger, "2027-01", 40000)
    reserve(ledger, "2027-03", 30000)
    expected = {
        "2026-12": (100000, 0, 40000),
        "2027-01": (40000, 60000, 60000),
        "2027-02": (60000, 40000, 100000),
        "2027-03": (100000, 0, 70000),
        "2027-04": (70000, 30000, 100000),
    }
    for month, (unassigned, set_aside, ready) in expected.items():
        summary = project(ledger, month)["summary"]
        assert (summary["carriedInUnassigned"], summary["carriedInSetAside"],
                summary["readyToAssign"]) == (unassigned, set_aside, ready)


def test_prebudget_imported_income_is_not_added_to_the_opening_snapshot_twice():
    ledger = state(opening=100000, start="2026-06")
    # The snapshot already includes imported pre-budget history.
    ledger["transactions"]["old-income"] = {
        "id": "old-income", "accountId": "cash", "date": "2026-05-10", "merchant": "Income",
        "amount": -70000, "categoryId": None, "pending": False, "reviewed": False,
        "source": "import", "note": "", "isTransfer": False,
    }
    post(ledger, "income", "2026-06", -20000, None)
    post(ledger, "expense", "2026-06", 5000)
    current = project(ledger, "2026-06")["summary"]
    assert current["carriedIn"] == 100000
    assert current["income"] == 20000
    assert current["readyToAssign"] == 120000
    assert current["fundingCapacity"] == 115000
    previous = calculate_workspace(ledger, "2026-05")["summary"]
    assert previous["carriedIn"] == previous["carriedInSetAside"] == previous["readyToAssign"] == 0
    assert previous["income"] == 70000


@pytest.mark.parametrize("kind", ["uncategorized", "overspend", "tracking-transfer"])
def test_current_unallocated_outflows_never_authorize_unsafe_assignments(kind):
    ledger = state(opening=10000)
    post(ledger, "outflow", "2026-01", 1000, None if kind != "overspend" else "food")
    if kind == "tracking-transfer":
        ledger["transactions"]["outflow"]["isTransfer"] = True
        ledger["accounts"]["tracking"] = {
            "id": "tracking", "type": "investment", "balance": 1000, "source": "manual",
        }
        post(ledger, "tracking-income", "2026-01", -1000, None, account="tracking")
    summary = project(ledger, "2026-01")["summary"]
    assert summary["readyToAssign"] == 10000
    assert summary["fundingCapacity"] == 9000
    for operation in (lambda: assign(ledger, "rent", "2026-01", 9001),
                      lambda: reserve(ledger, "2026-01", 9001)):
        before = deepcopy(ledger)
        with pytest.raises(StoreError, match="exceeds available"):
            operation()
        assert ledger == before
    assert project(ledger, "2026-02")["summary"]["carriedIn"] == (10000 if kind == "overspend" else 9000)


def test_credit_debt_and_payments_use_signed_snapshots_without_counting_income():
    ledger = state(opening=10000)
    ledger["accounts"]["card"] = {"id": "card", "type": "credit", "balance": 2000, "source": "manual"}
    post(ledger, "card-purchase", "2026-01", 1000, account="card")
    assign(ledger, "food", "2026-01", 1000)
    summary = project(ledger, "2026-01")["summary"]
    assert summary["income"] == 0
    assert summary["carriedIn"] == 8000
    assert summary["readyToAssign"] == summary["fundingCapacity"] == 7000
    post(ledger, "payment-out", "2026-01", 1500, None)
    post(ledger, "payment-in", "2026-01", -1500, None, account="card")
    for key in ("payment-out", "payment-in"):
        ledger["transactions"][key]["isTransfer"] = True
    assert project(ledger, "2026-01")["summary"] == summary


def test_unknown_credit_and_stale_cash_limit_only_their_own_currency():
    ledger = state()
    ledger["accounts"].update({
        "old": {"id": "old", "type": "depository", "balance": 999999, "stale": True},
        "card": {"id": "card", "type": "credit", "balance": 0, "balanceUnknown": True},
        "yen": {"id": "yen", "type": "depository", "balance": 1000, "currency": "JPY"},
    })
    usd = project(ledger, "2026-01")["summary"]
    assert usd["carriedIn"] == usd["readyToAssign"] == 100000
    assert usd["fundingCapacity"] == 0
    assert project(ledger, "2026-01", "JPY")["summary"]["fundingCapacity"] == 1000
    with pytest.raises(StoreError, match="exceeds available"):
        reserve(ledger, "2026-01", 1)
    reserve(ledger, "2026-01", 1000, "JPY")
    assert project(ledger, "2026-02", "JPY")["summary"]["carriedInSetAside"] == 1000


def test_large_minor_unit_amounts_are_exact_and_currency_local():
    ledger = state(opening=MAX_MINOR_UNITS, currency="KWD")
    reserve(ledger, "2026-01", MAX_MINOR_UNITS - 1, "KWD")
    january = project(ledger, "2026-01", "KWD")["summary"]
    assert january["readyToAssign"] == 1
    assert project(ledger, "2026-02", "KWD")["summary"]["carriedInSetAside"] == MAX_MINOR_UNITS - 1
    assert project(ledger, "2026-01", "USD")["summary"]["readyToAssign"] == 0


def test_assignment_can_cover_its_own_category_debt_without_borrowing_other_envelopes():
    ledger = state(opening=100000)
    post(ledger, "prior-overspend", "2026-01", 30000)
    february = project(ledger, "2026-02")
    assert february["summary"]["readyToAssign"] == 100000
    assert february["summary"]["fundingCapacity"] == 70000
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match="exceeds available"):
        assign(ledger, "rent", "2026-02", 100000)
    assert ledger == before
    assign(ledger, "food", "2026-02", 100000)
    february = project(ledger, "2026-02")
    assert february["budget"][0]["available"] == 70000
    assert february["summary"]["readyToAssign"] == february["summary"]["fundingCapacity"] == 0


def test_unknown_credit_still_blocks_increases_even_when_covering_category_debt():
    ledger = state(opening=100000)
    post(ledger, "prior-overspend", "2026-01", 30000)
    ledger["accounts"]["card"] = {
        "id": "card", "type": "credit", "balance": 0, "balanceUnknown": True,
    }
    with pytest.raises(StoreError, match="exceeds available"):
        assign(ledger, "food", "2026-02", 100000)


def test_rollover_keeps_negative_monthly_pools_and_worsened_cash_headroom_visible():
    ledger = state(opening=10000)
    ledger["accounts"]["card"] = {
        "id": "card", "type": "credit", "balance": 2000, "source": "manual",
    }
    ledger["budget"] = {"2026-01": {"rent": 300}, "2026-02": {"rent": 10000}}
    post(ledger, "unfunded-food", "2026-01", 800)
    # A payment from outside the budget reduces card debt, but is not income.
    post(ledger, "card-payment", "2026-02", -1000, None, account="card")
    ledger["rolloverModes"] = {"2026-01": {"USD": "balance"}}
    summary = project(ledger, "2026-02")["summary"]
    assert summary["readyToAssign"] == -2800
    before = deepcopy(ledger)
    mode(ledger, "2026-01", "category")
    assert ledger == {**before, "rolloverModes": {"2026-01": {"USD": "category"}}}
    february = project(ledger, "2026-02")
    # Rent retains its 300 while Food retains its -800. The monthly pool may
    # improve even though more cash is reserved in positive category balances.
    assert february["summary"]["readyToAssign"] == -2300
    assert {row["categoryId"]: row["carryover"] for row in february["budget"]} == {
        "food": -800, "rent": 300,
    }
    assert february["summary"]["available"] + february["summary"]["readyToAssign"] == 7200
    for operation in (
        lambda: assign(ledger, "rent", "2026-02", 10001),
        lambda: reserve(ledger, "2026-02", 1),
    ):
        with pytest.raises(StoreError, match="exceeds available"):
            operation()
    mode(ledger, "2026-01", "balance")
    assert ledger == before


@pytest.mark.parametrize("credit", [False, True])
def test_pooling_existing_category_debt_can_expose_a_deficit_without_worsening_cash(credit):
    ledger = state(opening=25000 if not credit else 40000)
    if credit:
        ledger["accounts"]["card"] = {
            "id": "card", "type": "credit", "balance": 15000, "source": "manual",
        }
    ledger["budget"] = {"2026-01": {"food": 10000}}
    post(ledger, "expense", "2026-01", 30000)
    before = project(ledger, "2026-02")["summary"]
    assert before["readyToAssign"] == before["carriedIn"] == 15000
    assert before["fundingCapacity"] == -5000
    mode(ledger, "2026-01", "balance")
    after = project(ledger, "2026-02")["summary"]
    assert after["readyToAssign"] == after["carriedIn"] == -5000
    assert after["balanceCarryover"] == -20000
    assert after["fundingCapacity"] == before["fundingCapacity"]
    mode(ledger, "2026-01", "category")
    assert project(ledger, "2026-02")["summary"] == before


@pytest.mark.parametrize("operation", ["assignment", "funding"])
def test_cash_limited_errors_explain_the_actual_constraint(operation):
    ledger = state(opening=10000)
    post(ledger, "uncategorized", "2026-01", 1000, None)
    with pytest.raises(StoreError, match="exceeds available money.*Review overspending"):
        if operation == "assignment":
            assign(ledger, "food", "2026-01", 9500)
        else:
            reserve(ledger, "2026-01", 9500)
