"""Explicit opening classifications preserve snapshots and never invent activity."""

from copy import deepcopy

import pytest

from whab.domain import budget, categorization, commands, transfer_matching
from whab.domain.categories import (
    classify_category,
    create_category,
    preview_category_classification,
)
from whab.domain.errors import StoreError
from whab.domain.review import effective_transfer, review_status
from whab.domain.transaction_changes import validate_transaction_changes
from whab.domain.transactions import transaction_rows


def account(identifier='cash', *, kind='depository', balance=50000, currency='USD', **fields):
    return {'id': identifier, 'name': 'Fictional account ' + identifier, 'institution': 'Fictional bank',
            'type': kind, 'balance': balance, 'currency': currency, 'source': 'fictional',
            'stale': False, 'balanceUnknown': False, 'onBudget': kind in {'depository', 'credit'},
            'subtype': None, **fields}


def transaction(identifier='opening', *, account_id='cash', amount=-20000, category='opening', **fields):
    return {'id': identifier, 'accountId': account_id, 'date': '2026-11-03', 'amount': amount,
            'merchant': '  Fictional imported amount  ', 'merchantLogo': None, 'categoryId': category,
            'pending': False, 'reviewed': False, 'source': 'ynab', 'note': '  [YNAB: cleared]\nOriginal text  ',
            'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True, 'deleted': False,
            **fields}


def ledger(*, accounts=None, transactions=None):
    return {'budgetStartMonth': '2026-10', 'accounts': accounts or {'cash': account()},
            'categories': {'opening': create_category('opening', 'Arbitrary label', 'Technical', kind='opening_balance'),
                           'food': create_category('food', 'Food', 'Living'),
                           'income': create_category('income', 'Pay', 'Technical', kind='income')},
            'transactions': transactions if transactions is not None else {'opening': transaction()},
            'budget': {}, 'futureFunding': {}, 'rolloverModes': {}}


def summary(state, month='2026-11', currency='USD'):
    return budget.select_currency(budget.calculate_workspace(state, month), currency)['summary']


@pytest.mark.parametrize('subtype', ['checking', 'savings', 'cash', 'money_market'])
def test_opening_cash_funds_once_without_income_or_spending_or_snapshot_changes(subtype):
    state = ledger(accounts={'cash': account(subtype=subtype)})
    before = deepcopy(state)
    results = [summary(state, month) for month in ('2026-10', '2026-11', '2026-12')]
    assert [row['readyToAssign'] for row in results] == [30000, 50000, 50000]
    assert [row['openingFunding'] for row in results] == [0, 20000, 0]
    assert [row['carriedIn'] for row in results] == [30000, 30000, 50000]
    for row in results:
        assert row['income'] == row['classifiedIncome'] == row['unclassifiedIncome'] == row['spent'] == 0
        assert row['readyToAssign'] == row['income'] + row['openingFunding'] + row['carriedIn'] - row['assigned'] - row['assignedNextMonth']
    assert state == before
    projection = budget.calculate_workspace(state, '2026-11')
    assert not any(row['categoryId'] == 'opening' for row in projection['budget'])
    assert projection['trend'][-1] == {'month': '2026-11', 'income': 0, 'spent': 0, 'openingFunding': 20000}
    assert budget.calculate_workspace_periods(state, '2026-10', 3) == [budget.calculate_workspace(state, month) for month in ('2026-10', '2026-11', '2026-12')]


@pytest.mark.parametrize('amount', [-10000, 0, 10000])
def test_signed_cash_opening_values_remain_verbatim_and_separate(amount):
    state = ledger(accounts={'cash': account(balance=-amount)}, transactions={'opening': transaction(amount=amount)})
    before = deepcopy(state)
    row = summary(state)
    assert row['openingFunding'] == row['readyToAssign'] == -amount
    assert row['income'] == row['spent'] == 0
    assert state == before


@pytest.mark.parametrize('amount', [-10000, 0, 10000])
def test_credit_opening_reserves_existing_debt_only_from_its_month(amount):
    state = ledger(accounts={'cash': account(), 'card': account('card', kind='credit', balance=amount)},
                   transactions={'opening': transaction(account_id='card', amount=amount)})
    before = deepcopy(state)
    prior, current, later = [summary(state, month) for month in ('2026-10', '2026-11', '2026-12')]
    assert prior['readyToAssign'] == 50000
    assert current['readyToAssign'] == later['readyToAssign'] == 50000 - max(amount, 0)
    assert all(row['openingFunding'] == row['income'] == row['spent'] == 0 for row in (prior, current, later))
    assert budget._net_account_funds(state, '2026-11', 'USD', opening=True) == current['carriedIn']
    assert state == before


@pytest.mark.parametrize('kind', ['credit', 'loan', 'investment', 'retirement', 'property', 'other_liability', 'depository'])
@pytest.mark.parametrize('amount', [-10000, 0, 10000])
def test_tracking_opening_assets_and_liabilities_do_not_fund_envelopes(kind, amount):
    state = ledger(accounts={'tracking': account('tracking', kind=kind, balance=amount, onBudget=False)},
                   transactions={'opening': transaction(account_id='tracking', amount=amount)})
    before = budget.calculate_workspace(state, '2026-11')['accountSummary']
    row = summary(state)
    assert row['openingFunding'] == row['income'] == row['spent'] == row['readyToAssign'] == row['fundingCapacity'] == 0
    assert budget.calculate_workspace(state, '2026-11')['accountSummary'] == before


@pytest.mark.parametrize('field', ['stale', 'balanceUnknown'])
def test_unknown_or_stale_cash_cannot_create_opening_funding(field):
    state = ledger(accounts={'cash': account(**{field: True})})
    row = summary(state)
    assert row['openingFunding'] == row['readyToAssign'] == row['fundingCapacity'] == 0


def test_explicitly_assigning_legacy_opening_inflow_preserves_its_funding():
    state = ledger(transactions={'opening': transaction(category=None, categoryEdited=False)})
    original = deepcopy(state['transactions']['opening'])
    previous = summary(state)
    commands.edit_transaction(state, 'opening', 'opening', category_agent='Codex')
    current = summary(state)
    assert previous['income'] == previous['unclassifiedIncome'] == current['openingFunding'] == 20000
    assert current['income'] == current['unclassifiedIncome'] == 0
    assert current['readyToAssign'] == previous['readyToAssign'] == 50000
    for key in ('id', 'date', 'merchant', 'amount', 'accountId', 'note', 'source', 'pending', 'reviewed'):
        assert state['transactions']['opening'][key] == original[key]
    assert state['accounts']['cash']['balance'] == 50000
    assert state['transactions']['opening']['categoryAgent'] == 'Codex'


def test_category_conversion_preview_separates_opening_from_income_without_mutation():
    state = ledger(transactions={'opening': transaction(category='income')})
    before = deepcopy(state)
    preview = preview_category_classification(state, 'income', 'opening_balance')
    impact, = preview['impacts']
    assert impact['month'] == '2026-11'
    assert impact['before']['income'] == impact['after']['openingFunding'] == 20000
    assert impact['before']['readyToAssign'] == impact['after']['readyToAssign'] == 50000
    assert state == before
    classify_category(state, 'income', 'opening_balance')
    assert state['transactions'] == before['transactions'] and state['accounts'] == before['accounts']
    assert state['categories']['income']['id'] == 'income'


@pytest.mark.parametrize('target,assigned', [(1, None), (0, 0), (0, 100)])
def test_opening_category_cannot_destroy_an_expense_target_or_assignment(target, assigned):
    state = ledger()
    state['categories']['food']['target'] = target
    if assigned is not None:
        state['budget']['2026-11'] = {'food': assigned}
    before = deepcopy(state)
    with pytest.raises(StoreError, match='targets or monthly assignments'):
        preview_category_classification(state, 'food', 'opening_balance')
    assert state == before


def test_opening_category_target_and_monthly_assignments_are_refused():
    with pytest.raises(StoreError, match='expense target'):
        create_category('bad', 'Opening', 'Technical', target=1, kind='opening_balance')
    state = ledger()
    with pytest.raises(StoreError, match='monthly assignments'):
        budget.ensure_assignment_allowed(state, 'opening', '2026-11', 0, '2026-11')


def test_existing_zero_opening_is_explicitly_reviewable_but_never_auto_reviewed():
    state = ledger(transactions={'opening': transaction(amount=0, category=None)})
    commands.edit_transaction(state, 'opening', 'opening')
    row = state['transactions']['opening']
    assert row['amount'] == 0 and not row['reviewed']
    assert review_status(row, state['categories']) == {'classification': 'opening_balance', 'eligible': True, 'reason': None}
    commands.edit_transaction(state, 'opening', reviewed=True)
    assert row['reviewed']
    commands.edit_transaction(state, 'opening', None)
    assert not row['reviewed'] and row['categoryId'] is None and row['amount'] == 0
    for edits, creates in [([{'transactionId': 'opening', 'amount': 0}], []),
                           ([], [{'creationId': '00000000-0000-4000-8000-000000000001', 'accountId': 'cash', 'date': '2026-11-03', 'merchant': 'Fictional', 'amount': 0, 'currency': 'USD', 'categoryId': 'opening'}])]:
        with pytest.raises(StoreError, match='not be zero'):
            validate_transaction_changes(edits, creates)


@pytest.mark.parametrize('field', ['pending', 'deleted'])
def test_pending_and_deleted_openings_cannot_be_reviewed_or_fund(field):
    state = ledger(transactions={'opening': transaction(**{field: True})})
    before = deepcopy(state)
    with pytest.raises(StoreError):
        commands.edit_transaction(state, 'opening', reviewed=True)
    assert state == before and not review_status(state['transactions']['opening'], state['categories'])['eligible']
    assert summary(state)['openingFunding'] == 0


def test_prebudget_openings_are_already_in_carried_cash_without_second_funding():
    state = ledger(transactions={'opening': transaction(date='2026-09-01')})
    initial = summary(state, '2026-10')
    assert initial['openingFunding'] == initial['income'] == 0
    assert initial['carriedIn'] == initial['readyToAssign'] == 50000
    assert summary(state, '2026-09')['readyToAssign'] == 0


def test_opening_classification_is_currency_scoped_and_never_inferred_from_names():
    state = ledger(accounts={'cash': account(), 'euro': account('euro', currency='EUR', balance=7000)},
                   transactions={'opening': transaction(), 'euro': transaction('euro', account_id='euro', category='euro-opening', amount=-3000),
                                 'ordinary': transaction('ordinary', category='food', amount=500, merchant='Starting Balance')})
    state['categories']['euro-opening'] = create_category('euro-opening', 'Anything', 'Other', currency='EUR', kind='opening_balance')
    usd, eur = summary(state), summary(state, currency='EUR')
    assert usd['openingFunding'] == 20000 and usd['spent'] == 500
    assert eur['openingFunding'] == 3000 and eur['spent'] == eur['income'] == 0
    assert summary(state, currency='JPY')['openingFunding'] == 0
    with pytest.raises(StoreError, match='currency'):
        commands.edit_transaction(state, 'opening', 'euro-opening')


def test_opening_category_overrides_transfer_hints_without_rewriting_source_flags():
    row = transaction(isTransfer=True, inferredTransfer=True)
    state = ledger(transactions={'opening': row})
    before = deepcopy(state)
    assert summary(state)['openingFunding'] == 20000
    assert not effective_transfer(row, state['categories'])
    assert transaction_rows(state)[0]['isTransfer'] is False
    assert state == before
    state['accounts']['other'] = account('other')
    state['transactions']['other'] = transaction('other', account_id='other', amount=20000, category=None)
    assert transfer_matching.candidates(state['accounts'], list(state['transactions'].values()), {}, categories=state['categories'])['total'] == 0
    with pytest.raises(StoreError, match='unclassified or explicitly Transfer'):
        transfer_matching.plan(state, ['opening', 'other'])


def test_opening_rows_do_not_train_or_receive_ordinary_category_suggestions():
    state = ledger(transactions={'opening': transaction(reviewed=True), 'food': transaction('food', category='food', reviewed=True),
                                 'unreviewed-opening': transaction('unreviewed-opening')})
    rows, accounts, categories = list(state['transactions'].values()), list(state['accounts'].values()), list(state['categories'].values())
    assert [row['id'] for row in categorization.candidates(rows, accounts, categories, reviewed=True)] == ['food']
    assert categorization.candidates(rows, accounts, categories, reviewed=False) == []


def test_conversion_preview_includes_empty_months_and_following_carry_boundary():
    state = ledger(transactions={'opening': transaction(category='food', date='2026-10-01')})
    state['futureFunding'] = {'2027-01': {'USD': 100}}
    before = deepcopy(state)
    preview = preview_category_classification(state, 'food', 'opening_balance')
    assert [row['month'] for row in preview['impacts']] == ['2026-10', '2026-11', '2026-12', '2027-01', '2027-02']
    assert preview['impacts'][0]['after']['openingFunding'] == 20000
    assert all(row['after']['openingFunding'] == 0 for row in preview['impacts'][1:])
    assert all(row['after']['readyToAssign'] > row['before']['readyToAssign'] for row in preview['impacts'])
    assert state == before


def test_linked_transfers_must_be_unmatched_before_opening_assignment_or_conversion():
    state = ledger(transactions={'opening': transaction(category='transfer', transferPeerId='peer', isTransfer=True)})
    state['categories']['transfer'] = create_category('transfer', 'Transfer', 'Technical', kind='transfer')
    before = deepcopy(state)
    with pytest.raises(StoreError, match='Unmatch'):
        commands.edit_transaction(state, 'opening', 'opening')
    with pytest.raises(StoreError, match='Unmatch'):
        preview_category_classification(state, 'transfer', 'opening_balance')
    assert state == before


def test_provider_refresh_preserves_explicit_zero_opening_annotations():
    from whab.domain.imports import annotated_transaction
    original = transaction(amount=0, reviewed=True, categoryAgent='Codex')
    record = {key: original[key] for key in ('id', 'accountId', 'date', 'merchant', 'amount', 'pending', 'source')}
    record.update(suggestedCategoryId='food', inferredTransfer=True, pendingTransactionId=None)
    saved = annotated_transaction(record, original, categories=ledger()['categories'])
    assert saved['categoryId'] == 'opening' and saved['amount'] == 0 and saved['reviewed']
    assert saved['note'] == original['note'] and saved['merchant'] == original['merchant']
    assert saved['categoryAgent'] == 'Codex' and saved['source'] == original['source']
    assert not saved['isTransfer'] and saved['inferredTransfer']


def test_preview_stops_at_supported_calendar_boundary():
    state = ledger(transactions={'opening': transaction(category='income', date='2299-12-01')})
    state['budgetStartMonth'] = '2299-12'
    impacts = preview_category_classification(state, 'income', 'opening_balance')['impacts']
    assert [row['month'] for row in impacts] == ['2299-12']
    assert impacts[0]['after']['openingFunding'] == 20000
