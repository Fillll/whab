"""Budget projections operate on plain records without opening SQLite or HTTP."""

from copy import deepcopy

import pytest

from whab.domain.budget import calculate_workspace, ensure_assignment_allowed, select_currency
from whab.domain.errors import StoreError
from whab.domain.money import MAX_SAFE_INTEGER


@pytest.fixture
def state():
    return {
        "budgetStartMonth": "2026-08",
        "accounts": {
            "cash": {"id": "cash", "type": "depository", "balance": 100000, "stale": False}
        },
        "categories": {
            "groceries": {
                "id": "groceries",
                "name": "Groceries",
                "group": "Everyday",
                "target": 20000,
            }
        },
        "transactions": {},
        "budget": {},
    }


def transaction(transaction_id, date, amount, *, pending=False, transfer=False):
    return {
        "id": transaction_id,
        "accountId": "cash",
        "date": date,
        "merchant": "Fixture grocer",
        "amount": amount,
        "categoryId": "groceries",
        "pending": pending,
        "reviewed": False,
        "source": "manual",
        "note": "",
        "isTransfer": transfer,
        "categoryEdited": False,
    }


def test_rollover_and_refund_preserve_money_without_mutating_state(state):
    state["budget"] = {"2026-08": {"groceries": 20000}, "2026-09": {"groceries": 10000}}
    state["transactions"] = {
        "purchase": transaction("purchase", "2026-08-10", 7000),
        "refund": transaction("refund", "2026-09-10", -500),
    }
    state["accounts"]["cash"]["balance"] = 93500
    before = deepcopy(state)
    view = calculate_workspace(state, "2026-09")
    category = view["budget"][0]
    assert (category["carryover"], category["spent"], category["available"]) == (13000, -500, 23500)
    assert view["summary"]["readyToAssign"] == 70000
    assert "categoryEdited" not in view["transactions"][0]
    assert state == before


def test_history_pending_and_transfers_do_not_consume_new_envelopes(state):
    state["budgetStartMonth"] = "2026-10"
    state["budget"] = {"2026-10": {"groceries": 20000}}
    state["transactions"] = {
        "history": transaction("history", "2024-11-01", 800000),
        "pending": transaction("pending", "2026-10-01", 6425, pending=True),
        "transfer": transaction("transfer", "2026-10-02", 6000, transfer=True),
    }
    view = calculate_workspace(state, "2026-10")
    assert view["budget"][0]["available"] == 20000
    assert view["summary"]["spent"] == 0
    assert view["summary"]["readyToAssign"] == 80000
    assert calculate_workspace(state, "2024-11")["summary"]["spent"] == 800000
    with pytest.raises(StoreError, match="before the budget start"):
        ensure_assignment_allowed(state, "groceries", "2024-11", 100, "2026-10")


def test_stale_cash_card_debt_and_negative_envelopes_do_not_invent_funding(state):
    state["accounts"]["old"] = {"id": "old", "type": "depository", "balance": 50000, "stale": True}
    state["accounts"]["card"] = {"id": "card", "type": "credit", "balance": 20000}
    state["categories"]["fun"] = {"id": "fun", "name": "Fun", "group": "Life", "target": 0}
    state["budget"] = {"2026-10": {"groceries": 10000}}
    spent = transaction("expense", "2026-10-01", 5000)
    spent["categoryId"] = "fun"
    state["transactions"]["expense"] = spent
    view = calculate_workspace(state, "2026-10")
    assert view["summary"]["available"] == 5000
    assert view["summary"]["readyToAssign"] == 70000
    state["accounts"]["card"]["balanceUnknown"] = True
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 0


def test_future_assignments_are_reserved_but_deficit_reductions_are_allowed(state):
    state["budget"] = {"2026-10": {"groceries": 20000}, "2026-11": {"groceries": 30000}}
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(state, "groceries", "2026-10", 85000, "2026-10")
    state["accounts"]["cash"]["balance"] = 0
    ensure_assignment_allowed(state, "groceries", "2026-10", 0, "2026-10")


def test_previous_overspending_is_outstanding_category_debt_entering_the_month(state):
    state["categories"].update({
        "fun": {"id": "fun", "name": "Fun", "group": "Life", "target": 0},
        "travel": {"id": "travel", "name": "Travel", "group": "Goals", "target": 0},
    })
    state["accounts"]["tracking"] = {
        "id": "tracking", "type": "investment", "balance": 50000,
    }
    state["budget"] = {
        "2026-07": {"groceries": 99999},
        "2026-08": {"groceries": 2000, "fun": 10000},
        "2026-09": {"groceries": 1000},
        "2026-10": {"groceries": 5000},
    }
    rows = [
        transaction("history", "2026-07-01", 99999),
        transaction("purchase", "2026-08-10", 6500),
        transaction("refund", "2026-09-10", -500),
        transaction("pending", "2026-09-15", 99999, pending=True),
        transaction("transfer", "2026-09-16", 99999, transfer=True),
        {**transaction("deleted", "2026-09-17", 99999), "deleted": True},
        {**transaction("tracking", "2026-09-18", 99999), "accountId": "tracking"},
        {**transaction("trip", "2026-08-20", 4000), "categoryId": "travel"},
        {**transaction("trip-refund", "2026-09-20", -1000), "categoryId": "travel"},
        transaction("current-spend", "2026-10-01", 9000),
        transaction("current-refund", "2026-10-02", -800),
    ]
    state["transactions"] = {row["id"]: row for row in rows}
    before = deepcopy(state)
    view = calculate_workspace(state, "2026-10")
    assert view["summary"]["previousOverspending"] == 6000
    assert {row["categoryId"]: row["carryover"] for row in view["budget"]} == {
        "groceries": -3000, "fun": 10000, "travel": -3000,
    }
    # Positive envelopes cannot offset debt, and this display total does not
    # deduct debt again from cash that already reflects the original spending.
    assert view["summary"]["readyToAssign"] == 90000
    assert view["summary"]["spent"] == 8200
    assert calculate_workspace(state, "2026-09")["summary"]["previousOverspending"] == 8500
    assert calculate_workspace(state, "2026-08")["summary"]["previousOverspending"] == 0
    assert calculate_workspace(state, "2026-07")["summary"]["previousOverspending"] == 0
    assert state == before


def test_next_month_assignments_cross_the_year_without_reserving_all_future_months(state):
    state["budget"] = {
        "2026-12": {"groceries": 2000},
        "2027-01": {"groceries": 3000},
        "2027-02": {"groceries": 4000},
    }
    december = calculate_workspace(state, "2026-12")
    assert december["summary"]["assignedNextMonth"] == 3000
    assert december["summary"]["readyToAssign"] == 98000
    assert calculate_workspace(state, "2027-01")["summary"]["assignedNextMonth"] == 4000
    assert calculate_workspace(state, "2027-02")["summary"]["assignedNextMonth"] == 0
    # The command still reserves every future assignment, independent of the
    # single following-month amount shown in the projection.
    ensure_assignment_allowed(state, "groceries", "2026-12", 93000, "2026-12")
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(state, "groceries", "2026-12", 93001, "2026-12")


@pytest.mark.parametrize("month", ["1900-01", "2200-12"])
def test_monthly_section_totals_support_public_month_boundaries(state, month):
    view = calculate_workspace(state, month)
    assert view["summary"]["previousOverspending"] == 0
    assert view["summary"]["assignedNextMonth"] == 0


def test_next_month_summary_rejects_an_unsafe_aggregate(state):
    state["categories"]["fun"] = {"id": "fun", "name": "Fun", "group": "Life", "target": 0}
    state["budget"] = {"2026-11": {"groceries": MAX_SAFE_INTEGER, "fun": 1}}
    with pytest.raises(StoreError, match="totals exceed"):
        calculate_workspace(state, "2026-10")


@pytest.fixture
def multicurrency_state(state):
    """Three currencies with different precision and independently funded envelopes."""
    state["accounts"] = {}
    state["categories"] = {}
    state["budget"] = {"2026-08": {}, "2026-09": {}}
    fixtures = (
        ("USD", 100000, 20000, 20000, 10000, 7000, -500, 12000),
        ("EUR", 50000, 7000, 8000, 6000, 2000, 2500, 9000),
        ("JPY", 3000, 200, 400, 300, 100, 200, 1000),
    )
    for code, cash, debt, prior_budget, assigned, prior_spend, spent, income in fixtures:
        account_id = f"cash-{code}"
        category_id = f"groceries-{code}"
        state["accounts"][account_id] = {
            "id": account_id, "type": "depository", "balance": cash, "currency": code,
        }
        state["accounts"][f"card-{code}"] = {
            "id": f"card-{code}", "type": "credit", "balance": debt, "currency": code,
        }
        state["categories"][category_id] = {
            "id": category_id, "name": "Groceries", "group": "Everyday",
            "target": assigned, "currency": code,
        }
        state["budget"]["2026-08"][category_id] = prior_budget
        state["budget"]["2026-09"][category_id] = assigned
        for label, date, amount, category in (
            ("prior", "2026-08-10", prior_spend, category_id),
            ("spend", "2026-09-10", spent, category_id),
            ("income", "2026-09-15", -income, None),
        ):
            key = f"{label}-{code}"
            row = transaction(key, date, amount)
            row.update(accountId=account_id, categoryId=category)
            state["transactions"][key] = row
    return state


def test_multiple_currencies_keep_cash_debt_income_and_rollover_separate(multicurrency_state):
    before = deepcopy(multicurrency_state)
    view = calculate_workspace(multicurrency_state, "2026-09")
    assert view["currencies"] == ["EUR", "JPY", "USD"]
    assert view["summariesByCurrency"] == {
        "USD": {"income": 12000, "spent": -500, "assigned": 10000,
                "available": 23500, "readyToAssign": 56500,
                "previousOverspending": 0, "assignedNextMonth": 0},
        "EUR": {"income": 9000, "spent": 2500, "assigned": 6000,
                "available": 9500, "readyToAssign": 33500,
                "previousOverspending": 0, "assignedNextMonth": 0},
        "JPY": {"income": 1000, "spent": 200, "assigned": 300,
                "available": 400, "readyToAssign": 2400,
                "previousOverspending": 0, "assignedNextMonth": 0},
    }
    assert view["summary"] == view["summariesByCurrency"]["USD"]
    by_currency = {row["currency"]: row for row in view["budget"]}
    assert {code: (row["carryover"], row["available"]) for code, row in by_currency.items()} == {
        "USD": (13000, 23500), "EUR": (6000, 9500), "JPY": (300, 400),
    }
    for code, income, spent in (("USD", 12000, -500), ("EUR", 9000, 2500), ("JPY", 1000, 200)):
        assert view["trendsByCurrency"][code][-1] == {
            "month": "2026-09", "income": income, "spent": spent,
        }
    assert multicurrency_state == before


def test_monthly_section_totals_keep_currencies_separate(multicurrency_state):
    multicurrency_state["budget"]["2026-08"]["groceries-EUR"] = 0
    multicurrency_state["budget"]["2026-08"]["groceries-JPY"] = 0
    multicurrency_state["budget"]["2026-10"] = {
        "groceries-USD": 30000, "groceries-EUR": 8000, "groceries-JPY": 500,
    }
    multicurrency_state["budget"]["2026-11"] = {"groceries-USD": 40000}
    view = calculate_workspace(multicurrency_state, "2026-09")
    assert {
        code: (row["previousOverspending"], row["assignedNextMonth"])
        for code, row in view["summariesByCurrency"].items()
    } == {"USD": (0, 30000), "EUR": (2000, 8000), "JPY": (100, 500)}


def test_later_transactions_reconstruct_only_their_own_currency_cash(multicurrency_state):
    view = calculate_workspace(multicurrency_state, "2026-08")
    assert {code: row["readyToAssign"] for code, row in view["summariesByCurrency"].items()} == {
        "USD": 54500, "EUR": 30500, "JPY": 1700,
    }


def test_unknown_credit_and_stale_cash_restrict_only_their_own_currency(multicurrency_state):
    multicurrency_state["accounts"]["card-EUR"]["balanceUnknown"] = True
    multicurrency_state["accounts"]["stale-USD"] = {
        "id": "stale-USD", "type": "depository", "balance": 999999,
        "stale": True, "currency": "USD",
    }
    view = calculate_workspace(multicurrency_state, "2026-09")
    assert {code: row["readyToAssign"] for code, row in view["summariesByCurrency"].items()} == {
        "USD": 56500, "EUR": 0, "JPY": 2400,
    }


def test_assignment_reserves_only_future_assignments_in_the_same_currency(multicurrency_state):
    multicurrency_state["budget"]["2026-10"] = {
        "groceries-USD": 30000, "groceries-EUR": 900000, "groceries-JPY": 500,
    }
    # USD has 56,500 unassigned and reserves 30,000 for October: an increase
    # of 26,500 is funded, regardless of the separate EUR oversubscription.
    ensure_assignment_allowed(multicurrency_state, "groceries-USD", "2026-09", 36500, "2026-09")
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(multicurrency_state, "groceries-USD", "2026-09", 36501, "2026-09")
    ensure_assignment_allowed(multicurrency_state, "groceries-JPY", "2026-09", 2200, "2026-09")
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(multicurrency_state, "groceries-JPY", "2026-09", 2201, "2026-09")
    # A reduction remains allowed when that same currency is overallocated.
    ensure_assignment_allowed(multicurrency_state, "groceries-EUR", "2026-09", 0, "2026-09")


def test_account_and_category_currency_mismatch_is_rejected(multicurrency_state):
    multicurrency_state["transactions"]["spend-USD"]["categoryId"] = "groceries-EUR"
    with pytest.raises(StoreError, match="currency must match"):
        calculate_workspace(multicurrency_state, "2026-09")


@pytest.mark.parametrize(("code", "places"), [("USD", 2), ("EUR", 2), ("JPY", 0)])
def test_currency_selection_scopes_every_projection(multicurrency_state, code, places):
    view = calculate_workspace(multicurrency_state, "2026-09")
    before = deepcopy(view)
    selected = select_currency(view, code)
    assert selected["currency"] == code
    assert selected["currencyMinorUnits"] == places
    assert {row["id"] for row in selected["accounts"]} == {f"cash-{code}", f"card-{code}"}
    assert {row["id"] for row in selected["categories"]} == {f"groceries-{code}"}
    assert {row["id"] for row in selected["transactions"]} == {f"spend-{code}", f"income-{code}"}
    assert {row["categoryId"] for row in selected["budget"]} == {f"groceries-{code}"}
    assert [row["id"] for group in selected["categoryGroups"] for row in group["children"]] == [
        f"groceries-{code}"
    ]
    assert selected["summary"] == view["summariesByCurrency"][code]
    assert selected["trend"] == view["trendsByCurrency"][code]
    assert view == before


def test_currency_without_records_has_an_empty_projection(multicurrency_state):
    view = select_currency(calculate_workspace(multicurrency_state, "2026-09"), "KWD")
    assert view["currency"] == "KWD"
    assert view["currencyMinorUnits"] == 3
    assert "KWD" in view["currencies"]
    assert all(view[key] == [] for key in ("accounts", "categories", "categoryGroups",
                                         "transactions", "budget"))
    assert set(view["summary"].values()) == {0}
    assert view["summary"]["previousOverspending"] == view["summary"]["assignedNextMonth"] == 0
    assert view["trend"] == [
        {"month": month, "income": 0, "spent": 0}
        for month in ("2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09")
    ]


def test_select_currency_rejects_invalid_currency(multicurrency_state):
    with pytest.raises(StoreError, match="ISO 4217"):
        select_currency(calculate_workspace(multicurrency_state, "2026-09"), "ZZZ")
