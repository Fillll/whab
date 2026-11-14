"""Explicit Income treatment, legacy compatibility and native guarded persistence."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.categories import create_category, validate_child_category
from whab.domain.errors import StoreError
from whab.plugins import LedgerService, RequestConflict, RevisionConflict


@pytest.fixture(params=['sqlite', 'json'])
def income_ledger(tmp_path, request):
    factory = SqliteStore if request.param == 'sqlite' else JsonStore
    path = tmp_path / ('fictional.sqlite' if request.param == 'sqlite' else 'fictional.json')
    store = factory(path, month='2026-11')
    service = LedgerService(store)
    cash = service.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
    tracking = service.add_account('personal', {'name': 'Fictional tracking', 'type': 'investment', 'balance': 0, 'onBudget': False})
    credit = service.add_account('personal', {'name': 'Fictional credit', 'type': 'credit', 'balance': 0, 'onBudget': True})
    salary = service.add_category('personal', {'name': '給与 🍏', 'group': 'Finance', 'target': 0})
    fees = service.add_category('personal', {'name': 'Fees', 'group': 'Finance', 'target': 0})
    tx = service.add_transaction('personal', {'accountId': cash, 'merchant': 'Fictional employer',
        'date': '2026-11-01', 'amount': -500000, 'categoryId': None, 'note': ' [YNAB: fictional]\nexact  '})
    service.edit_transaction('personal', tx, reviewed=True)
    yield store, service, factory, path, (cash, tracking, credit, salary, fees, tx)
    store.close()


def command(service, operation, payload, *, revision=None, request=None):
    return service.command(operation, 'personal', payload, expected_revision=revision or service.revision,
        request_id=request or uuid4().hex, history={'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'income-fixture'})


def summary(service, month='2026-11'):
    return service.query('workspace', 'personal', month=month)['data']['summary']


def test_salary_classification_funds_once_review_independent_and_no_upgrade_rewrite(income_ledger):
    store, service, factory, path, (cash, tracking, credit, salary, fees, tx) = income_ledger
    before = deepcopy(store.export_budget())
    original = summary(service)
    assert (original['income'], original['classifiedIncome'], original['unclassifiedIncome']) == (500000, 0, 500000)
    assert original['readyToAssign'] == 500000
    preview = service.query('preview_category_classification', 'personal', parameters={'categoryId': salary, 'kind': 'income'})
    assert store.export_budget() == before and preview['data']['before']['id'] == salary
    command(service, 'classify_category', {'categoryId': salary, 'kind': 'income'})
    command(service, 'edit_transactions', {'edits': [{'transactionId': tx, 'categoryId': salary, 'categoryAgent': 'Codex'}]})
    after = summary(service)
    assert (after['income'], after['classifiedIncome'], after['unclassifiedIncome'], after['spent'], after['readyToAssign']) == (500000, 500000, 0, 0, 500000)
    saved = next(t for t in store.transactions('personal') if t['id'] == tx)
    assert saved['reviewed'] is True and saved['note'] == before['transactions'][0]['note']
    assert salary not in {row['categoryId'] for row in store.workspace('personal', '2026-11')['budget']}
    assert {row.get('kind', 'expense') for group in store.category_tree('personal') if group['name'] == 'Finance' for row in group['children']} == {'income', 'expense'}
    command(service, 'edit_transaction', {'transactionId': tx, 'categoryId': None})
    assert summary(service) == original and store.transactions('personal')[0]['reviewed'] is True
    snapshot = deepcopy(store.export_budget())
    store.close()
    reopened = factory(path, month='2026-11')
    try:
        assert reopened.export_budget() == snapshot
    finally:
        reopened.close()
    # Fixture teardown owns the original handle; close is idempotent for both adapters.


def test_income_refunds_reversals_pending_tracking_transfers_and_currencies(income_ledger, tmp_path):
    store, service, factory, _, (cash, tracking, credit, salary, fees, tx) = income_ledger
    command(service, 'classify_category', {'categoryId': salary, 'kind': 'income'})
    command(service, 'edit_transaction', {'transactionId': tx, 'categoryId': salary})
    def add(account, amount, category, **extra):
        identifier = service.add_transaction('personal', {'accountId': account, 'merchant': 'Fictional',
            'date': '2026-11-02', 'amount': amount, 'categoryId': category})
        return identifier
    add(cash, 10000, salary)  # Income reversal, not spending.
    add(cash, -2000, fees)  # Expense reimbursement, not income.
    add(tracking, -700000, salary)
    add(credit, -800000, salary)
    # Public portable restore seeds imported pending/transfer flags and verbatim text.
    pending, transfer = add(cash, -900000, salary), add(cash, -600000, None)
    snapshot = store.export_budget()
    for row in snapshot['transactions']:
        if row['id'] == pending:
            row['pending'] = True
        if row['id'] == transfer:
            row.update(isTransfer=True, inferredTransfer=True)
        row['merchant'] = '  Fictional employer  '
    store = factory(tmp_path / 'imported-fictional', month='2026-11')
    store.restore_budget('personal', {key: value for key, value in snapshot.items() if key != 'history'})
    service = LedgerService(store)
    actual = summary(service)
    assert (actual['income'], actual['classifiedIncome'], actual['unclassifiedIncome'], actual['spent']) == (490000, 490000, 0, -2000)
    command(service, 'edit_transactions', {'edits': [{'transactionId': tx, 'categoryId': salary, 'reviewed': False}]})
    assert all(row['merchant'] == '  Fictional employer  ' for row in store.transactions('personal'))
    euro = service.add_category('personal', {'name': 'Euro salary', 'group': 'Finance', 'target': 0, 'currency': 'EUR', 'kind': 'income'})
    euro_account = service.add_account('personal', {'name': 'Euro cash', 'type': 'depository', 'balance': 0, 'currency': 'EUR'})
    add(euro_account, -12345, euro)
    projection = store.workspace('personal', '2026-11')
    assert projection['summariesByCurrency']['EUR']['income'] == 12345
    assert projection['summariesByCurrency']['USD']['income'] == 490000
    store.close()


def test_configuration_guards_preview_replay_history_and_restore(income_ledger, tmp_path):
    store, service, factory, _, (cash, tracking, credit, salary, fees, tx) = income_ledger
    command(service, 'edit_transaction', {'transactionId': tx, 'categoryId': salary})
    before = deepcopy(store.export_budget())
    preview = service.query('preview_category_classification', 'personal', parameters={'categoryId': salary, 'kind': 'income'})['data']
    impact = next(row for row in preview['impacts'] if row['month'] == '2026-11')
    assert (impact['before']['income'], impact['after']['income']) == (0, 500000)
    assert (impact['before']['readyToAssign'], impact['after']['readyToAssign']) == (0, 500000)
    assert store.export_budget() == before
    revision, request = service.revision, uuid4().hex
    args = {'categoryId': salary, 'kind': 'income'}
    generation = store.commit_evidence().generation
    result = command(service, 'classify_category', args, revision=revision, request=request)
    assert store.commit_evidence().generation == generation + 1
    assert command(service, 'classify_category', args, revision=revision, request=request) == result
    assert store.commit_evidence().generation == generation + 1
    with pytest.raises(RequestConflict):
        command(service, 'classify_category', {**args, 'kind': 'expense'}, revision=revision, request=request)
    with pytest.raises(RevisionConflict):
        command(service, 'classify_category', {**args, 'kind': 'expense'}, revision=revision)
    saved = store.export_budget()
    assert saved['transactions'] == before['transactions'] and saved['assignments'] == before['assignments']
    entry = store.budget_history('personal')['entries'][0]
    assert entry['operation'] == 'classify_category' and entry['changeCount'] == 1 and entry['reversible'] is True
    undo = store.history_preview('personal', 'undo', target=entry['id'])
    store.history_apply('personal', 'undo', entry['id'], undo['fingerprint'])
    assert sorted(store.export_budget()['categories'], key=lambda row: row['id']) == sorted(before['categories'], key=lambda row: row['id'])
    redo = store.history_preview('personal', 'redo', target=entry['id'])
    store.history_apply('personal', 'redo', entry['id'], redo['fingerprint'])
    assert sorted(store.export_budget()['categories'], key=lambda row: row['id']) == sorted(saved['categories'], key=lambda row: row['id'])
    destination = factory(tmp_path / 'restored', month='2026-11')
    try:
        destination.restore_budget_files('personal', store.export_budget_files())
        assert destination.export_budget() == store.export_budget()
        assert destination.workspace('personal', '2026-11')['summary'] == summary(LedgerService(store))
    finally:
        destination.close()
    for operation, payload in [('assign', {'categoryId': salary, 'month': '2026-11', 'assigned': 0}),
        ('add_category', {'name': 'Invalid', 'group': 'Finance', 'target': 1, 'kind': 'income'}),
        ('classify_category', {'categoryId': salary, 'kind': 'other'})]:
        before = store.export_budget()
        with pytest.raises(StoreError):
            command(service, operation, payload)
        assert store.export_budget() == before
    command(service, 'assign', {'categoryId': fees, 'month': '2026-11', 'assigned': 0})
    with pytest.raises(StoreError, match='including zero'):
        command(service, 'classify_category', {'categoryId': fees, 'kind': 'income'})


def test_kind_validation_and_unsupported_backends():
    for kind in [None, False, 'other']:
        with pytest.raises(StoreError):
            create_category('salary', 'Salary', 'Finance', kind=kind)
    with pytest.raises(StoreError, match='expense target'):
        validate_child_category({'id': 'salary', 'name': 'Salary', 'group': 'Finance', 'target': 1, 'kind': 'income'})
    service = LedgerService(SimpleNamespace())
    with pytest.raises(StoreError, match='Update the storage'):
        command(service, 'classify_category', {'categoryId': 'salary', 'kind': 'income'})
    for parameters in [{}, {'categoryId': 'salary', 'kind': 'other'}]:
        with pytest.raises(StoreError):
            service.query('preview_category_classification', 'personal', parameters=parameters)
