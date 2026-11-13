"""Current-state review invariants through the real public native contracts."""
from copy import deepcopy
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.system_categories import system_category
from whab.plugins import LedgerService, RevisionConflict, StoreError


@pytest.fixture(params=[SqliteStore, JsonStore])
def ledger(request, tmp_path):
    path = tmp_path / 'fictional'
    store = request.param(path, month='2026-11')
    service = LedgerService(store)
    account = service.add_account('personal', {'name': '旅行 🍏', 'type': 'depository', 'balance': 100000})
    tx = service.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional shop', 'amount': 100, 'categoryId': None, 'note': ' [YNAB: cleared] \n  '})
    yield store, service, tx, request.param, path
    store.close()


def apply(service, tx, changes, **kwargs):
    return service.command('edit_transactions', 'personal', {'edits': [{'transactionId': tx, **changes}]},
                           expected_revision=kwargs.get('revision', service.revision), request_id=kwargs.get('request_id', uuid4().hex))


def test_review_clear_and_exact_restart_retry(ledger):
    store, service, tx, factory, path = ledger
    before, evidence = store.export_budget(), store.commit_evidence()
    for operation, payload in [('edit_transaction', {'transactionId': tx, 'reviewed': True}),
                               ('review_transactions', {'transactionIds': [tx], 'reviewed': True}),
                               ('edit_transactions', {'edits': [{'transactionId': tx, 'reviewed': True, 'note': 'must roll back'}]})]:
        with pytest.raises(StoreError, match='Categorize'):
            service.command(operation, 'personal', payload, expected_revision=service.revision, request_id=uuid4().hex)
        assert store.export_budget() == before and store.commit_evidence() == evidence
    for kind in ('transfer', 'income'):
        apply(service, tx, {'reviewed': False})
        selected = system_category(kind)['id']
        apply(service, tx, {'categoryId': selected, 'categoryAgent': 'Codex'})
        assert not next(row for row in store.transactions('personal') if row['id'] == tx)['reviewed']
        apply(service, tx, {'reviewed': True})
        assert next(row for row in store.transactions('personal') if row['id'] == tx)['reviewed']
    revision, intent = service.revision, uuid4().hex
    from whab.plugins.storage_transfer import (
        StorageOperation,
        canonical_storage_json,
        read_storage_json,
    )
    evidence = store.commit_evidence()
    operation = StorageOperation(intent, evidence.authority_epoch, evidence.generation, 'personal', 'edit_transactions', canonical_storage_json({'edits': [{'transactionId': tx, 'categoryId': None, 'appendNote': 'Exact appended text'}]}))
    saved = store.execute_operation(operation)
    row = read_storage_json(saved.receipt.result)['changes'][0]['after']
    assert row['categoryId'] is None and row['reviewed'] is False
    assert row['note'] == ' [YNAB: cleared] \n  \nExact appended text'
    after = store.export_budget()
    store.close()
    reopened = factory(path, month='2026-11')
    try:
        again = LedgerService(reopened)
        replayed = reopened.execute_operation(operation)
        assert replayed.replayed and replayed.receipt.result == saved.receipt.result
        assert reopened.export_budget() == after
        with pytest.raises(RevisionConflict):
            apply(again, tx, {'reviewed': False}, revision=revision)
    finally:
        reopened.close()


def test_restore_legacy_current_records_keeps_immutable_history_and_ambiguity(ledger, tmp_path):
    store, service, tx, factory, _ = ledger
    # A synthetic old export: no system identities, a cleared ambiguous inflow and
    # an authoritative transfer. History baseline remains an exact old snapshot.
    snapshot = store.export_budget()
    snapshot.pop('history', None)
    snapshot['categories'] = [row for row in snapshot['categories'] if not row.get('systemKind')]
    row = snapshot['transactions'][0]
    row.update(reviewed=True, amount=-100, merchant='  imported whitespace  ')
    transfer = {**row, 'id': 'fictional-transfer', 'isTransfer': True, 'inferredTransfer': True}
    snapshot['transactions'].append(transfer)
    from whab.domain.history import record_financial
    journal = record_financial(None, snapshot, snapshot, {'action': 'snapshot'})
    snapshot['history'] = deepcopy(journal)
    target = factory(tmp_path / 'restored', month='2026-11')
    try:
        target.restore_budget('personal', snapshot)
        restored = target.export_budget()
        rows = {row['id']: row for row in restored['transactions']}
        assert rows[tx]['categoryId'] is None and rows[tx]['reviewed'] is False
        assert rows[tx]['merchant'] == '  imported whitespace  '
        assert rows['fictional-transfer']['categoryId'] == system_category('transfer')['id']
        assert rows['fictional-transfer']['reviewed'] is True
        assert restored['history']['baseline'] == journal['baseline']
        assert restored['history']['entries'][:len(journal['entries'])] == journal['entries']
        assert restored['history']['entries'][-1]['operation'] == 'migrate_system_categories'
        # Agent annotation paths cannot trim unrelated imported merchant text.
        target.edit_transaction_details('personal', tx, {'categoryId': 'groceries', 'categoryAgent': 'Codex'})
        assert next(row for row in target.transactions('personal') if row['id'] == tx)['merchant'] == '  imported whitespace  '
        target.close()
        target = factory(tmp_path / 'restored', month='2026-11')
        unchanged = target.export_budget()
        assert len(unchanged['history']['entries']) == len(restored['history']['entries']) + 1
    finally:
        target.close()


def test_system_categories_are_not_expense_envelopes_or_user_name_aliases(ledger):
    store, service, tx, _, _ = ledger
    income = system_category('income')['id']
    transfer = system_category('transfer')['id']
    shadow = service.add_category('personal', {'name': 'Income', 'group': 'User', 'target': 100})
    assert shadow != income
    baseline = store.workspace('personal', '2026-11')['summary']
    # Explicit Income contributes to the pool, with an opposite-sign reversal;
    # a refund classified to an expense envelope stays a category refund.
    account = store.transactions('personal')[0]['accountId']
    for amount, category in [(-1000, income), (200, income), (-50, 'groceries'), (300, transfer)]:
        service.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional movement', 'amount': amount, 'categoryId': category})
    view = store.workspace('personal', '2026-11')
    assert view['summary']['income'] == baseline['income'] + 800
    assert view['summary']['spent'] == baseline['spent'] - 50
    assert not {income, transfer} & {row['categoryId'] for row in view['budget']}
    assert {income, transfer} <= {row['id'] for row in store.categories('personal')}
    before = store.export_budget()
    for category in (income, transfer):
        assert store.category_deletion_status('personal', category)['deletable'] is False
        for action in (lambda: store.assign('personal', category, '2026-11', 0),
                       lambda: store.rename_category('personal', category, 'Other'),
                       lambda: store.delete_category('personal', category),
                       lambda: store.move_category('personal', category, 'missing')):
            with pytest.raises(StoreError, match='System categories'):
                action()
    assert store.export_budget() == before


def test_unsupported_classification_backend_refuses_transaction_changes_before_mutation():
    from types import SimpleNamespace
    service = LedgerService(SimpleNamespace())
    with pytest.raises(StoreError, match='Update the budget host/storage'):
        service.command('edit_transaction', 'personal', {'transactionId':'fictional', 'reviewed': True}, expected_revision=service.revision, request_id=uuid4().hex)


def test_readiness_requires_verified_contract_and_failed_migration_does_not_publish_ready():
    from types import SimpleNamespace
    status = {'supported': True, 'pending': True}
    def refuse(_):
        raise StoreError('Synthetic unresolved storage intent')
    service = LedgerService(SimpleNamespace(transaction_classification_status=lambda _: status, migrate_transaction_classifications=refuse))
    with pytest.raises(StoreError, match='unresolved'):
        service.query('categories','personal')
    assert not getattr(service, '_classifications_ready_for', None)
    status['supported'] = 'true'
    with pytest.raises(StoreError, match='readiness'):
        service.query('categories','personal')
