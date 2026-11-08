"""Fictional journals survive native commits, loss, restart and portable copies."""

from copy import deepcopy
from uuid import uuid4

import pytest

from whab.domain.errors import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json
from whab.services.ledger import LedgerService


@pytest.fixture(params=['sqlite', 'json'])
def history_store(request, tmp_path):
    if request.param == 'sqlite':
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    path = tmp_path / ('ledger.' + request.param)
    backend = Store(path, month='2026-11')
    yield backend, Store, path
    backend.close()


def command(backend, name, payload, metadata=None):
    evidence = backend.commit_evidence()
    body = {'operation': name, 'parameters': payload, 'context': metadata or {'actor':'Codex', 'actorKind':'agent', 'batchId':'fictional-batch', 'operation':name}}
    operation = StorageOperation(str(uuid4()), evidence.authority_epoch, evidence.generation, 'personal', 'recorded_command', canonical_storage_json(body))
    return operation, backend.execute_operation(operation)


def test_native_atomic_history_replay_reopen_export_and_restore(history_store, tmp_path):
    backend, factory, path = history_store
    assert backend.budget_history('personal')['total'] == 0
    for arguments in ({'target':'missing'}, {'snapshot':True, 'target':'baseline'}):
        with pytest.raises(StoreError):
            backend.budget_history('personal', **arguments)
    operation, result = command(backend, 'add_account', {'name':'Fictional checking', 'type':'depository', 'balance':10000})
    first = backend.budget_history('personal')['entries'][0]
    assert first['actor'] == 'Codex' and first['batchId'] == 'fictional-batch'
    assert backend.budget_history('demo')['total'] == 0
    assert backend.execute_operation(operation).replayed
    assert backend.budget_history('personal')['total'] == 1
    assert backend.budget_history('personal', target=first['id'])['entries'][0]['changes'][0]['path'][0] == 'accounts'
    assert backend.budget_history('personal', snapshot=True, target='baseline')['snapshot']['accounts'] == []
    saved = deepcopy(backend.export_budget('personal')['history'])
    portable = backend.export_budget_files('personal')
    backend.close()
    reopened = factory(path)
    try:
        assert reopened.export_budget('personal')['history'] == saved
        preview = reopened.history_preview('personal', 'undo', first['id'])
        command(reopened, 'history_apply', {'action':'undo', 'target':first['id'], 'fingerprint':preview['fingerprint']}, {'operation':'history', 'action':'undo', 'target':first['id']})
        assert reopened.accounts('personal') == []
        redo = reopened.history_preview('personal', 'redo', first['id'])
        command(reopened, 'history_apply', {'action':'redo', 'target':first['id'], 'fingerprint':redo['fingerprint']}, {'operation':'history', 'action':'redo', 'target':first['id']})
        assert len(reopened.accounts('personal')) == 1
        assert reopened.budget_history('personal')['total'] == 3
        assert reopened.export_budget('personal')['history']['entries'][0] == saved['entries'][0]
    finally:
        reopened.close()
    fresh = factory(tmp_path / 'copy')
    try:
        fresh.restore_budget_files('personal', portable)
        assert fresh.export_budget('personal')['history']['entries'][:1] == saved['entries']
        assert len(fresh.accounts('personal')) == 1
    finally:
        fresh.close()
    assert result.receipt.operation_id == operation.operation_id


def test_failed_native_publication_keeps_history_and_money_together(history_store, monkeypatch):
    backend, _, _ = history_store
    command(backend, 'add_account', {'name':'Fictional', 'type':'depository', 'balance':10000})
    before = backend.export_budget('personal')
    if hasattr(backend, 'db'):
        backend.db.execute("CREATE TRIGGER fail_history BEFORE UPDATE ON budget_history BEGIN SELECT RAISE(ABORT,'fictional history failure'); END")
    else:
        def fail(candidate):
            raise OSError('fictional history failure')
        monkeypatch.setattr(backend, '_commit', fail)
    with pytest.raises(Exception, match='fictional history failure'):
        command(backend, 'add_account', {'name':'Must roll back', 'type':'depository', 'balance':200})
    assert backend.export_budget('personal') == before


def test_service_batches_cas_and_snapshot_are_explicit(history_store):
    backend, _, _ = history_store
    service = LedgerService(backend)
    account = service.add_account('personal', {'name':'Fictional', 'type':'depository', 'balance':10000})
    transaction = service.add_transaction('personal', {'accountId':account, 'date':'2026-11-01', 'merchant':'Fictional shop', 'amount':100, 'categoryId':None})
    for category in ['groceries', 'dining']:
        service.command('edit_transaction', 'personal', {'transactionId':transaction, 'categoryId':category, 'categoryAgent':'Codex'}, request_id=str(uuid4()), expected_revision=service.revision, history={'batchId':'fictional-categories'})
    preview = service.query('history_preview', 'personal', parameters={'action':'undo', 'target':'fictional-categories'})
    payload = {k:v for k,v in preview['data'].items() if k != 'changes'}
    response = service.command('history_apply', 'personal', payload, request_id='undo-once', expected_revision=preview['revision'])
    assert service.command('history_apply', 'personal', payload, request_id='undo-once', expected_revision=preview['revision']) == response
    assert service.transactions('personal')[0]['categoryId'] is None
    p = service.query('history_preview', 'personal', parameters={'action':'snapshot'})
    service.command('history_apply', 'personal', {k:v for k,v in p['data'].items() if k != 'changes'}, request_id='snapshot', expected_revision=p['revision'])
    assert backend.budget_history('personal')['entries'][0]['hasSnapshot']
    assert backend.budget_history('personal', limit=1)['next']


def test_history_outlives_bounded_retry_receipts_and_cannot_be_overwritten(history_store, monkeypatch):
    backend, _, _ = history_store
    if hasattr(backend, 'db'):
        from whab_plugin_sqlite import authority
    else:
        from whab_plugin_json import authority
    monkeypatch.setattr(authority, 'MAX_OPERATION_RECEIPTS', 2)
    operations = [command(backend, 'add_account', {'name':f'Fictional {i}', 'type':'depository', 'balance':100})[0] for i in range(4)]
    assert backend.operation_receipt(operations[0].operation_id) is None
    assert backend.budget_history('personal')['total'] == 4
    first = backend.budget_history('personal')['entries'][-1]['id']
    assert backend.budget_history('personal', snapshot=True, target=first)['snapshot']['accounts']
    assert backend.budget_history('personal', snapshot=True, target='baseline')['snapshot']['accounts'] == []


def test_receipt_barriers_log_without_leaking_attachment_bytes(history_store):
    from whab_plugin_devkit import receipt_png
    backend, _, _ = history_store
    service = LedgerService(backend)
    account = service.add_account('personal', {'name':'Fictional', 'type':'depository','balance':1000})
    transaction = service.add_transaction('personal', {'accountId':account,'date':'2026-11-01','merchant':'Fictional receipt','amount':100,'categoryId':None})
    backend.save_receipt('personal', transaction, receipt_png())
    entry = backend.budget_history('personal')['entries'][0]
    assert entry['operation'] == 'save_receipt' and entry['changeCount'] == 0
    assert 'PNG' not in str(backend.export_budget('personal')['history'])
    with pytest.raises(StoreError, match='attachment'):
        backend.history_preview('personal', 'restore', 'baseline')
    backend.delete_receipt('personal', transaction)
    assert backend.budget_history('personal')['entries'][0]['operation'] == 'delete_receipt'


def test_durable_pristine_restore_adopts_history_without_synthetic_events(history_store):
    from whab.domain import commands
    from whab.domain.ledger_state import initial_state, portable_snapshot
    backend, _, _ = history_store
    state = initial_state('personal', '2026-11')
    commands.add_account(state, 'fictional-account', {'name':'Fictional restore','type':'depository','balance':100})
    operation, _ = command(backend, 'restore_budget', {'snapshot':portable_snapshot(state)})
    assert backend.execute_operation(operation).replayed
    assert backend.budget_history('personal')['total'] == 0
    command(backend, 'add_account', {'name':'First new change','type':'depository','balance':0})
    baseline = backend.budget_history('personal', snapshot=True, target='baseline')['snapshot']
    assert baseline['accounts'][0]['id'] == 'fictional-account'


def test_snapshot_only_history_cannot_be_replaced_by_a_pristine_restore(history_store):
    from whab.domain.ledger_state import initial_state, portable_snapshot
    backend, _, _ = history_store
    preview = backend.history_preview('personal', 'snapshot')
    backend.history_apply('personal', 'snapshot', None, preview['fingerprint'])
    before = backend.export_budget('personal')
    with pytest.raises(StoreError, match='history|empty'):
        backend.restore_budget('personal', portable_snapshot(initial_state('personal','2026-11')))
    assert backend.export_budget('personal') == before


def test_raw_native_history_commands_preserve_action_without_a_host_metadata_scope(history_store):
    backend, _, _ = history_store
    command(backend, 'add_account', {'name':'Fictional raw SDK','type':'depository','balance':100})
    target = backend.budget_history('personal')['entries'][0]['id']
    for action, identifier in [('undo',target),('redo',target),('snapshot',None)]:
        preview = backend.history_preview('personal', action, identifier)
        evidence = backend.commit_evidence()
        operation = StorageOperation(str(uuid4()), evidence.authority_epoch, evidence.generation, 'personal', 'history_apply', canonical_storage_json({'action':action,'target':identifier,'fingerprint':preview['fingerprint']}))
        backend.execute_operation(operation)
        entry = backend.budget_history('personal')['entries'][0]
        assert entry['action'] == action and entry['target'] == identifier
    assert backend.budget_history('personal')['entries'][0]['hasSnapshot']
