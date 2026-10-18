"""Budget scope belongs to core rules, including history and currency boundaries."""

from copy import deepcopy

import pytest

from whab.domain.accounts import account_summary, is_liability, normalize_account
from whab.domain.budget import calculate_workspace, ensure_assignment_allowed, select_currency
from whab.domain.commands import add_account, add_transaction, update_account
from whab.domain.errors import StoreError
from whab.domain.money import MAX_SAFE_INTEGER


@pytest.mark.parametrize("kind,expected", [
    ("depository", True), ("credit", True), ("investment", False), ("retirement", False),
    ("property", False), ("loan", False), ("deposit", False), ("other_asset", False),
    ("other_liability", False), ("other", False),
])
def test_legacy_scope_defaults_are_core_rules(kind, expected):
    account = {"type": kind}
    assert normalize_account(account) == {"type": kind, "onBudget": expected, "subtype": None}
    assert account == {"type": kind}


@pytest.mark.parametrize("fields", [
    {"type": "unknown"}, {"type": []}, {"type": "depository", "onBudget": 1},
    {"type": "depository", "onBudget": None}, {"type": "retirement", "onBudget": True},
    {"type": "investment", "subtype": []}, {"type": "loan", "subtype": "roth_ira"},
])
def test_invalid_classification_never_enters_ledger(fields):
    with pytest.raises(StoreError):
        normalize_account(fields)


@pytest.fixture
def state():
    return {
        "budgetStartMonth": "2026-09", "accounts": {}, "transactions": {}, "budget": {},
        "categories": {"food": {"id": "food", "name": "Food", "group": "Daily", "target": 0}},
    }


def test_off_budget_activity_and_snapshots_never_fund_or_consume_envelopes(state):
    add_account(state, "checking", {"name": "Checking", "type": "depository", "balance": 10000})
    for identifier, kind, subtype in (
        ("cd", "deposit", "cd"), ("ira", "retirement", "ira"),
        ("roth", "retirement", "roth_ira"), ("401k", "retirement", "401k"),
        ("brokerage", "investment", "brokerage"), ("house", "property", "home"),
        ("car", "property", "vehicle"), ("mortgage", "loan", "mortgage"),
        ("auto", "loan", "auto_loan"), ("student", "loan", "student_loan"),
    ):
        add_account(state, identifier, {"name": identifier, "type": kind, "subtype": subtype, "balance": 900000})
        for suffix, date, amount in (("past", "2026-09-02", 100), ("current", "2026-10-02", -200)):
            add_transaction(state, identifier + suffix, {
                "accountId": identifier, "date": date, "merchant": "Tracking change",
                "amount": amount, "categoryId": "food",
            })
    state["budget"] = {"2026-09": {"food": 1000}, "2026-10": {"food": 2000}}
    before = deepcopy(state)
    result = calculate_workspace(state, "2026-10")
    assert result["summary"] == {
        "income": 0, "spent": 0, "assigned": 2000, "available": 3000, "readyToAssign": 7000,
        "previousOverspending": 0, "balanceCarryover": 0, "assignedNextMonth": 0,
        "carriedIn": 9000, "carriedInUnassigned": 9000, "carriedInSetAside": 0,
        "fundingCapacity": 7000,
    }
    assert result["budget"][0]["carryover"] == 1000
    assert all(row["income"] == row["spent"] == 0 for row in result["trend"])
    assert len(result["transactions"]) == 10
    assert state == before
    with pytest.raises(StoreError, match="exceeds available money"):
        ensure_assignment_allowed(state, "food", "2026-10", 9001, "2026-10")


def test_scope_edits_apply_to_existing_history_without_moving_money(state):
    add_account(state, "cash", {"name": "Savings", "type": "depository", "balance": 10000, "subtype": "savings"})
    add_transaction(state, "expense", {"accountId": "cash", "date": "2026-10-01", "merchant": "Shop", "amount": 1000, "categoryId": "food"})
    assert calculate_workspace(state, "2026-10")["summary"]["spent"] == 1000
    history = deepcopy(state["transactions"])
    update_account(state, "cash", {"onBudget": False})
    assert state["accounts"]["cash"]["balance"] == 9000
    result = calculate_workspace(state, "2026-10")
    assert result["summary"]["spent"] == result["summary"]["readyToAssign"] == 0
    assert result["accountSummary"]["netWorth"] == 9000
    assert state["transactions"] == history
    update_account(state, "cash", {"type": "credit"})
    assert state["accounts"]["cash"]["onBudget"] is False
    assert state["accounts"]["cash"]["subtype"] is None
    update_account(state, "cash", {"onBudget": True})
    update_account(state, "cash", {"type": "loan"})
    assert state["accounts"]["cash"]["onBudget"] is False
    previous = deepcopy(state)
    for identifier, changes in (("missing", {"onBudget": False}), ("cash", {}), ("cash", {"balance": 0}), ("cash", {"onBudget": True}), ("cash", {"type": []})):
        with pytest.raises(StoreError):
            update_account(state, identifier, changes)
        assert state == previous


def test_off_budget_unknown_credit_does_not_block_budget_and_debt_payments_reduce_liability(state):
    add_account(state, "cash", {"name": "Checking", "type": "depository", "balance": 10000})
    add_account(state, "card", {"name": "Tracking card", "type": "credit", "balance": 100000, "onBudget": False})
    state["accounts"]["card"]["balanceUnknown"] = True
    add_account(state, "mortgage", {"name": "Mortgage", "type": "loan", "subtype": "mortgage", "balance": 500000})
    add_transaction(state, "payment", {"accountId": "mortgage", "date": "2026-10-03", "merchant": "Principal payment", "amount": -10000, "categoryId": None})
    assert state["accounts"]["mortgage"]["balance"] == 490000
    assert is_liability(state["accounts"]["mortgage"])
    assert calculate_workspace(state, "2026-10")["summary"]["readyToAssign"] == 10000


def test_account_totals_keep_currencies_separate_and_skip_unknown_or_stale(state):
    for identifier, kind, balance, currency in (
        ("cash", "depository", 1000, "USD"), ("ira", "retirement", 5000, "USD"),
        ("loan", "loan", 2000, "USD"), ("euro-house", "property", 9000, "EUR"),
        ("euro-loan", "loan", 3000, "EUR"), ("overdraft", "depository", -100, "USD"),
        ("card-credit", "credit", -200, "USD"),
    ):
        add_account(state, identifier, {"name": identifier, "type": kind, "balance": balance, "currency": currency})
    state["accounts"]["stale"] = {"id": "stale", "type": "investment", "balance": 999999, "stale": True}
    state["accounts"]["unknown"] = {"id": "unknown", "type": "loan", "balance": 999999, "balanceUnknown": True}
    view = calculate_workspace(state, "2026-10")
    assert view["accountSummary"] == {"assets": 6200, "liabilities": 2100, "netWorth": 4100, "onBudget": 1100, "offBudget": 3000}
    assert select_currency(view, "EUR")["accountSummary"]["netWorth"] == 6000
    assert select_currency(view, "JPY")["accountSummary"]["netWorth"] == 0
    with pytest.raises(StoreError, match="totals exceed"):
        account_summary([{"type": "property", "balance": MAX_SAFE_INTEGER}, {"type": "property", "balance": 1}])
