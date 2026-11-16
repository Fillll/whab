"""Reversible native tombstones preserve source evidence, balances and durable intent."""

import json
import sqlite3
from contextlib import closing
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JSONStore
from whab_plugin_sqlite import Store as SQLiteStore

from tests.receipt_images import png
from whab.domain.accounts import normalize_account
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def fictional_state(count=4):
    state = initial_state('personal', '2026-10')
    state['accounts'] = {
        key: normalize_account({'id': key, 'name': name, 'institution': '', 'type': kind,
            'balance': 100000, 'currency': 'USD', 'source': 'manual', 'stale': False,
            'balanceUnknown': False, 'onBudget': True})
        for key, name, kind in [('cash', 'Fictional наличные', 'depository'), ('card', 'Fictional credit', 'credit')]
    }
    state['transactions'] = {
        f'tx-{index}': {'id': f'tx-{index}', 'accountId': 'cash' if index % 2 == 0 else 'card',
            'date': '2026-10-31', 'merchant': '  Fictional 商店  ', 'merchantLogo': None,
            'amount': (index + 1) * 100, 'categoryId': 'groceries', 'pending': False, 'reviewed': True,
            'source': 'ynab' if index < 2 else 'manual', 'note': '[YNAB: uncleared]\nExact memo  ',
            'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True,
            'deleted': False, 'categoryAgent': 'Prior agent', 'original': {'merchant': ' Imported source  '}}
        for index in range(count)
    }
    return state


@pytest.fixture(params=[SQLiteStore, JSONStore], ids=['sqlite', 'json'])
def void_store(request, tmp_path):
    path = tmp_path / ('fictional.sqlite' if request.param is SQLiteStore else 'fictional.json')
    store = request.param(path, month='2026-11')
    store.restore_budget('personal', portable_snapshot(fictional_state()))
    yield store, path, request.param
    store.close()


def recorded(store, identifiers, action='void', fingerprint=None):
    evidence = store.commit_evidence()
    return StorageOperation(uuid4().hex, evidence.authority_epoch, evidence.generation,
        'personal', 'recorded_command', canonical_storage_json({
            'operation': 'apply_transaction_void', 'parameters': {'transactionIds': identifiers,
                'action': action, 'fingerprint': fingerprint},
            'context': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-void-batch',
                        'operation': action + '_transactions'},
        }))


def apply_void(store, identifiers, action):
    proposal = store.preview_transaction_void('personal', identifiers, action)
    return store.apply_transaction_void('personal', identifiers, action, proposal['fingerprint'])


def rows(store):
    return {row['id']: row for row in store.export_budget('personal')['transactions']}


def portable_version(files):
    if 'budget.json' in files:
        return json.loads(files['budget.json'])['formatVersion']
    with closing(sqlite3.connect(':memory:')) as db:
        db.deserialize(files['budget.sqlite'])
        return db.execute('PRAGMA user_version').fetchone()[0]


def test_atomic_mixed_void_unvoid_history_restart_and_receipt_restore(void_store, tmp_path):
    store, path, Store = void_store
    identifiers = list(rows(store))
    store.save_receipt('personal', 'tx-0', png())
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    untouched_demo = store.export_budget('demo')
    assert store.supports_transaction_voiding()
    proposal = store.preview_transaction_void('personal', identifiers, 'void')
    assert store.export_budget('personal') == before and store.commit_evidence() == evidence
    assert {row['accountId']: row['after'] - row['before'] for row in proposal['balanceChanges']} == {'cash': 300, 'card': -400}
    operation = recorded(store, identifiers, fingerprint=proposal['fingerprint'])
    result = store.execute_operation(operation)
    assert store.commit_evidence().generation == evidence.generation + 1
    assert store.transactions('personal') == [] and store.receipt_ids('personal') == []
    assert store.export_budget('demo') == untouched_demo
    saved = store.export_budget('personal')
    assert saved['assignments'] == before['assignments']
    for old, row in zip(before['transactions'], saved['transactions']):
        assert row['voided'] and row['deleted'] and not row['reviewed']
        assert {k: v for k, v in row.items() if k not in {'voided', 'deleted', 'reviewed'}} == {
            k: v for k, v in old.items() if k not in {'deleted', 'reviewed'}}
    assert store.execute_operation(operation).receipt == result.receipt
    assert store.export_budget('personal') == saved
    with pytest.raises(StoreError):
        store.execute_operation(replace(operation, operation_id=uuid4().hex))
    with pytest.raises(StoreError):
        store.execute_operation(replace(operation, payload=canonical_storage_json({
            'operation': 'apply_transaction_void', 'parameters': {'transactionIds': ['tx-0'],
                'action': 'void', 'fingerprint': None}, 'context': {'actor': 'Other', 'actorKind': 'agent'}})))
    files = store.export_budget_files()
    assert portable_version(files) == 3
    restored = Store(tmp_path / ('restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        assert rows(restored) == rows(store)
        proposal = restored.preview_transaction_void('personal', identifiers, 'unvoid')
        restored.apply_transaction_void('personal', identifiers, 'unvoid', proposal['fingerprint'])
        assert restored.get_receipt('personal', 'tx-0') == png()
        assert all(not row['deleted'] and not row['reviewed'] and row.get('voided') is False for row in rows(restored).values())
        assert [a['balance'] for a in restored.export_budget('personal')['accounts']] == [a['balance'] for a in before['accounts']]
        assert portable_version(restored.export_budget_files()) == 3
    finally:
        restored.close()
    store.close()
    reopened = Store(path, month='2026-11')
    try:
        assert reopened.execute_operation(operation).receipt == result.receipt
        assert reopened.commit_evidence() == result.receipt.evidence
        entry = next(row for row in reopened.budget_history('personal')['entries'] if row['operation'] == 'void_transactions')
        assert entry['actor'] == 'Codex' and entry['batchId'] == 'fictional-void-batch'
        assert entry['changeCount'] == 6 and entry['reversible']
        for action in ('undo', 'redo'):
            proposal = reopened.history_preview('personal', action, entry['id'])
            reopened.history_apply('personal', action, entry['id'], proposal['fingerprint'])
            assert portable_version(reopened.export_budget_files()) == 3
        assert rows(reopened) == {row['id']: row for row in saved['transactions']}
    finally:
        reopened.close()


@pytest.mark.parametrize('identifiers,action', [
    (['tx-0', 'missing'], 'void'), (['tx-0', 'tx-0'], 'void'), (['tx-0'], 'unvoid'),
    ([], 'void'), (['tx-0'] * 101, 'void'), (['tx-0'], 'delete'),
])
def test_invalid_batch_preserves_everything(void_store, identifiers, action):
    store, _, _ = void_store
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    with pytest.raises(StoreError):
        store.apply_transaction_void('personal', identifiers, action, '0' * 64)
    assert store.export_budget('personal') == before and store.commit_evidence() == evidence


def test_stale_fingerprint_rejects_before_write(void_store):
    store, _, _ = void_store
    proposal = store.preview_transaction_void('personal', ['tx-0'], 'void')
    store.edit_transaction('personal', 'tx-0', note='Changed since preview')
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    with pytest.raises(StoreError, match='proposal changed'):
        store.apply_transaction_void('personal', ['tx-0'], 'void', proposal['fingerprint'])
    assert store.export_budget('personal') == before and store.commit_evidence() == evidence


def test_sqlite_100_record_failure_rolls_back_and_success_is_one_commit(tmp_path):
    with closing(SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')) as store:
        store.restore_budget('personal', portable_snapshot(fictional_state(100)))
        identifiers = list(rows(store))
        proposal = store.preview_transaction_void('personal', identifiers, 'void')
        operation = recorded(store, identifiers, fingerprint=proposal['fingerprint'])
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        store.db.execute("CREATE TRIGGER fail_void BEFORE UPDATE OF voided ON transactions WHEN NEW.id='tx-99' BEGIN SELECT RAISE(ABORT,'fictional void fault'); END")
        with pytest.raises(sqlite3.IntegrityError, match='fictional void fault'):
            store.execute_operation(operation)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        assert store.operation_receipt(operation.operation_id) is None
        store.db.execute('DROP TRIGGER fail_void')
        statements = []
        store.db.set_trace_callback(statements.append)
        store.execute_operation(operation)
        store.db.set_trace_callback(None)
        assert statements.count('COMMIT') == 1
        assert store.commit_evidence().generation == evidence.generation + 1
        assert all(row['voided'] for row in rows(store).values())


def test_first_bounded_void_save_fences_legacy_payload_and_retains_history(void_store, tmp_path, monkeypatch):
    store, path, Store = void_store
    store.edit_transaction('personal', 'tx-0', note='Warm fictional history')
    legacy = store.export_budget_files()
    assert portable_version(legacy) == 1
    with monkeypatch.context() as bounded:
        if Store is SQLiteStore:
            bounded.setattr(store, 'export_budget', lambda *args: pytest.fail('Bounded void save must not rebuild the full ledger'))
        proposal = store.preview_transaction_void('personal', ['tx-0'], 'void')
        store.execute_operation(recorded(store, ['tx-0'], fingerprint=proposal['fingerprint']))
        files = store.export_budget_files()
        assert portable_version(files) == 3
    proposal = store.preview_transaction_void('personal', ['tx-0'], 'unvoid')
    store.execute_operation(recorded(store, ['tx-0'], action='unvoid', fingerprint=proposal['fingerprint']))
    assert not any(row.get('voided') for row in rows(store).values())
    assert portable_version(store.export_budget_files()) == 3
    native = json.loads(path.read_bytes()) if Store is JSONStore else store._authority_metadata()
    assert native['formatVersion'] == 6
    restored = Store(tmp_path / ('history-only' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', store.export_budget_files())
        assert portable_version(restored.export_budget_files()) == 3
        assert rows(restored) == rows(store)
    finally:
        restored.close()


@pytest.mark.parametrize('amount', [0, 100])
def test_paired_void_requires_both_ids_preserves_archived_links_and_restores_unreviewed(void_store, amount):
    store, original_path, Store = void_store
    state = fictional_state(2)
    state['transactions']['tx-0'].update(amount=amount, categoryId=None, reviewed=False)
    state['transactions']['tx-1'].update(amount=-amount, categoryId=None, reviewed=False)
    # Existing imported zeros are restored into a fresh isolated ledger.
    path = original_path.parent / ('pair-' + str(amount))
    with closing(Store(path, month='2026-11')) as paired:
        paired.restore_budget('personal', portable_snapshot(state))
        paired.apply_transfer_matches('personal', [{'outId': 'tx-0', 'inId': 'tx-1'}], reviewed=True)
        before, evidence = paired.export_budget('personal'), paired.commit_evidence()
        with pytest.raises(StoreError, match='counterpart'):
            apply_void(paired, ['tx-0'], 'void')
        assert paired.export_budget('personal') == before and paired.commit_evidence() == evidence
        apply_void(paired, ['tx-0', 'tx-1'], 'void')
        assert all(row.get('transferPeerId') and row['voided'] and not row['reviewed'] for row in rows(paired).values())
        assert paired.export_budget('personal')['accounts'] == before['accounts']
        with pytest.raises(StoreError, match='counterpart'):
            apply_void(paired, ['tx-1'], 'unvoid')
        apply_void(paired, ['tx-0', 'tx-1'], 'unvoid')
        assert all(row['transferPairValid'] and not row['reviewed'] for row in paired.transactions('personal'))


def test_provider_refresh_removal_and_posting_preserve_void_tombstones(void_store, imported_account, imported_transaction, sync_changes, tmp_path):
    store, path, Store = void_store
    store.save_item('item', 'fictional-token', 'Fictional bank')
    pending = imported_transaction('pending', pending=True, merchant='  Exact imported merchant  ', amount=123)
    store.apply_sync('item', sync_changes(added=[pending]), 'one', [imported_account()])
    store.save_receipt('personal', 'pending', png())
    apply_void(store, ['pending'], 'void')
    frozen = rows(store)['pending']
    store.apply_sync('item', sync_changes(modified=[{**pending, 'merchant': 'Provider changed', 'amount': 999}]), 'two')
    assert rows(store)['pending'] == frozen
    store.apply_sync('item', sync_changes(removed=['pending']), 'three')
    assert rows(store)['pending']['providerDeleted']
    with pytest.raises(StoreError, match='provider removed'):
        apply_void(store, ['pending'], 'unvoid')
    store.apply_sync('item', sync_changes(added=[pending]), 'four')
    assert rows(store)['pending'] == frozen
    posted = imported_transaction('posted', pendingTransactionId='pending', amount=145)
    store.apply_sync('item', sync_changes(added=[posted]), 'five')
    current = rows(store)
    assert current['pending'] == {**frozen, 'providerDeleted': True}
    assert current['posted']['voided'] and current['posted']['deleted'] and current['posted']['amount'] == 145
    # A replay with both identities known must continue honoring the pending tombstone.
    store.apply_sync('item', sync_changes(modified=[{**posted, 'amount': 999}]), 'six')
    assert rows(store) == current
    apply_void(store, ['posted'], 'unvoid')
    store.apply_sync('item', sync_changes(modified=[{**posted, 'amount': 177}]), 'seven')
    assert rows(store)['posted']['voided'] is False and not rows(store)['posted']['deleted']
    assert rows(store)['posted']['amount'] == 177
    apply_void(store, ['posted'], 'void')
    current = rows(store)
    files = store.export_budget_files()
    restored = Store(tmp_path / ('posting-restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        assert rows(restored) == current
        apply_void(restored, ['posted'], 'unvoid')
        assert rows(restored)['posted']['voided'] is False
        assert restored.get_receipt('personal', 'posted') == png()
        # Both original and posted receipt identities remain in the archived file.
        if Store is SQLiteStore:
            assert restored.db.execute("SELECT COUNT(*) FROM transaction_receipts WHERE transaction_id IN ('pending','posted')").fetchone()[0] == 2
        else:
            assert set(restored._read('personal')['receipts']) == {'pending', 'posted'}
    finally:
        restored.close()


@pytest.mark.parametrize('backend', ['sqlite', 'json'])
def test_process_loss_after_void_commit_replays_without_second_save(tmp_path, backend):
    import subprocess
    import sys
    from pathlib import Path

    path, request_path = tmp_path / ('fictional.' + backend), tmp_path / 'request.json'
    child = '''
import json, os, runpy, sys
from pathlib import Path
helpers=runpy.run_path(sys.argv[1])
Store=helpers['SQLiteStore'] if sys.argv[2]=='sqlite' else helpers['JSONStore']
store=Store(sys.argv[3],month='2026-11')
store.restore_budget('personal',helpers['portable_snapshot'](helpers['fictional_state']()))
ids=['tx-0','tx-1','tx-2','tx-3']
preview=store.preview_transaction_void('personal',ids,'void')
command=helpers['recorded'](store,ids,fingerprint=preview['fingerprint'])
Path(sys.argv[4]).write_text(json.dumps({'operation_id':command.operation_id,'authority_epoch':command.authority_epoch,
 'expected_generation':command.expected_generation,'workspace':command.workspace,'name':command.name,'payload':command.payload.decode()}))
store.execute_operation(command)
os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', child, str(Path(__file__).resolve()), backend,
        str(path), str(request_path)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    data = json.loads(request_path.read_text())
    operation = StorageOperation(**{**data, 'payload': data['payload'].encode()})
    Store = SQLiteStore if backend == 'sqlite' else JSONStore
    with closing(Store(path, month='2026-11')) as store:
        saved, evidence = store.export_budget('personal'), store.commit_evidence()
        assert evidence.generation == operation.expected_generation + 1
        assert store.execute_operation(operation).receipt.evidence == evidence
        assert store.export_budget('personal') == saved and store.commit_evidence() == evidence
        assert len([row for row in store.budget_history('personal')['entries'] if row['operation'] == 'void_transactions']) == 1


def test_sqlite_unmanaged_sanitized_image_uses_same_atomic_void_rules(tmp_path):
    with closing(SQLiteStore(tmp_path / 'image.sqlite', month='2026-11', _authority=False)) as store:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        before = store.accounts('personal')
        result = apply_void(store, ['tx-0', 'tx-1'], 'void')
        assert result['count'] == 2 and all(row['before'] == row['after'] for row in result['balanceChanges'])
        assert all(rows(store)[key]['voided'] for key in ('tx-0', 'tx-1'))
        assert store.accounts('personal') == before


@pytest.mark.parametrize('column,value', [('voided', 2), ('provider_deleted', 2)])
def test_portable_rejects_malformed_tombstone_flags_before_restore(tmp_path, column, value):
    from whab_plugin_sqlite.portable import read_files

    with closing(SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')) as store:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        apply_void(store, ['tx-0'], 'void')
        files = store.export_budget_files()
        with closing(sqlite3.connect(':memory:')) as image:
            image.deserialize(files['budget.sqlite'])
            image.execute(f'UPDATE transactions SET {column}=? WHERE id=?', (value, 'tx-0'))
            image.commit()
            corrupted = {'budget.sqlite': image.serialize()}
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        with pytest.raises(StoreError, match='integer booleans'):
            read_files(corrupted)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
