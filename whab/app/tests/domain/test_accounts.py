"""Budget scope belongs to core rules, including history and currency boundaries."""

from copy import deepcopy

import pytest

from whab.domain.accounts import (
    ACCOUNT_TYPES,
    LIABILITY_ACCOUNT_TYPES,
    account_balance_position,
    account_summary,
    is_liability,
    normalize_account,
)
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
        "income": 0, "classifiedIncome": 0, "unclassifiedIncome": 0, "openingFunding": 0, "transferFunding": 0, "spent": 0, "assigned": 2000, "available": 3000, "readyToAssign": 7000,
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


@pytest.mark.parametrize('kind', ACCOUNT_TYPES)
@pytest.mark.parametrize('balance', [-500123, 0, 500123])
@pytest.mark.parametrize('source', ['manual', 'plaid', 'custom-feed', None])
def test_account_position_owns_positive_owes_negative_without_changing_account(kind, balance, source):
    account = {'type': kind, 'balance': balance, 'currency': 'KWD', 'closed': True}
    if source is not None:
        account['source'] = source
    before = deepcopy(account)
    expected = -balance if kind in LIABILITY_ACCOUNT_TYPES else balance
    assert account_balance_position(account) == {'value': expected, 'status': 'current'}
    assert account == before


@pytest.mark.parametrize('kind', ['credit', 'loan', 'other_liability', 'depository', 'other'])
@pytest.mark.parametrize('balance', [-12345, 0, 12345])
@pytest.mark.parametrize('flags', [{}, {'balanceUnknown': False, 'stale': False}, {'balanceUnknown': True, 'stale': True}])
def test_ynab_import_provenance_always_preserves_signed_estimate_and_excludes_totals(kind, balance, flags):
    account = {'type': kind, 'balance': balance, 'source': 'ynab', **flags}
    before = deepcopy(account)
    assert account_balance_position(account) == {'value': balance, 'status': 'estimated'}
    assert account_summary([account]) == dict.fromkeys(('assets', 'liabilities', 'netWorth', 'onBudget', 'offBudget'), 0)
    assert account == before


@pytest.mark.parametrize('value', [None, True, False, 123.0, '123', [], {}, MAX_SAFE_INTEGER + 1, -MAX_SAFE_INTEGER - 1])
@pytest.mark.parametrize('source', ['manual', 'ynab'])
def test_invalid_balance_has_no_fabricated_position_or_summary(value, source):
    account = {'type': 'loan', 'balance': value, 'source': source}
    before = deepcopy(account)
    assert account_balance_position(account) == {'value': None, 'status': 'unavailable'}
    assert account_summary([account])['netWorth'] == 0
    assert account == before


def test_missing_unknown_and_stale_balances_retain_their_evidence_status():
    assert account_balance_position({'type': 'credit'}) == {'value': None, 'status': 'unavailable'}
    for amount in (-12345, 0, 12345):
        account = {'type': 'credit', 'balance': amount, 'stale': True}
        assert account_balance_position(account) == {'value': -amount, 'status': 'stale'}
        assert account_summary([account])['netWorth'] == 0
        account['balanceUnknown'] = True
        assert account_balance_position(account) == {'value': None, 'status': 'unavailable'}


@pytest.mark.parametrize('kind', ['depository', 'credit'])
@pytest.mark.parametrize('value', [-MAX_SAFE_INTEGER, MAX_SAFE_INTEGER])
def test_position_preserves_safe_integer_boundary_and_aggregate_still_checks_overflow(kind, value):
    account = {'type': kind, 'balance': value}
    expected = -value if kind == 'credit' else value
    assert account_balance_position(account) == {'value': expected, 'status': 'current'}
    assert account_summary([account])['netWorth'] == expected
    with pytest.raises(StoreError, match='totals exceed'):
        account_summary([account, {'type': kind, 'balance': 1 if value > 0 else -1}])


def test_public_position_helper_and_positive_owed_summary_contract():
    from whab.plugins import account_balance_position as public_position
    assert public_position is account_balance_position
    accounts = [
        {'type': 'depository', 'balance': 10000},
        {'type': 'depository', 'balance': -2000},
        {'type': 'loan', 'subtype': 'mortgage', 'balance': 5000},
        {'type': 'credit', 'balance': -3000},
        {'type': 'loan', 'balance': -990000, 'source': 'ynab'},
    ]
    assert account_summary(accounts) == {'assets': 13000, 'liabilities': 7000, 'netWorth': 6000, 'onBudget': 11000, 'offBudget': -5000}


def test_bank_link_replaces_ynab_estimate_with_canonical_snapshot_without_transaction_changes(state):
    from whab.domain.bank_setup import configure_sync
    state.update(items={'item': {'provider': 'plaid', 'token': 'fictional-token'}}, receipts={})
    add_account(state, 'imported', {'name': 'Imported card', 'type': 'credit', 'balance': -90000})
    state['accounts']['imported'].update(source='ynab', balanceUnknown=True, stale=True)
    assert account_balance_position(state['accounts']['imported']) == {'value': -90000, 'status': 'estimated'}
    transactions = deepcopy(state['transactions'])
    bank = {'id': 'provider-card', 'name': 'Provider card', 'institution': 'Fictional Bank', 'type': 'credit',
            'balance': 80000, 'currency': 'USD', 'source': 'plaid', 'itemId': 'item'}
    configure_sync(state, 'item', [bank], {'provider-card': {'accountId': 'imported', 'since': None}})
    linked = state['accounts']['imported']
    assert linked['source'] == 'plaid' and linked['balance'] == 80000
    assert account_balance_position(linked) == {'value': -80000, 'status': 'current'}
    assert state['transactions'] == transactions
