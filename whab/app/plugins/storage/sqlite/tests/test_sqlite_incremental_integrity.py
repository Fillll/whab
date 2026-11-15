"""Native Merkle migration preserves durable commands and external-write checks."""

from dataclasses import replace

import pytest
from whab_plugin_sqlite import Store

from whab.domain.history import validate_history
from whab.plugins import StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    encode_local_state,
    local_state_digest,
    read_storage_json,
)


def request(store, identifier, name, payload):
    evidence = store.commit_evidence()
    return StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
                            'personal', name, canonical_storage_json(payload))


def test_legacy_open_then_atomic_migration_reopen_retry_and_rollback(tmp_path):
    path = tmp_path / 'fictional.sqlite'
    store = Store(path, month='2026-10')
    original = store.commit_evidence()
    assert original.digest_version == 1
    store.close()
    store = Store(path)
    assert store.commit_evidence() == original
    command = request(store, 'fictional-create', 'add_account', {'name': 'Fictional cash', 'type': 'depository', 'balance': 123})
    result = store.execute_operation(command)
    account = read_storage_json(result.receipt.result)
    assert result.receipt.evidence.digest_version == 2
    assert result.receipt.evidence.generation == original.generation + 1
    assert result.receipt.evidence.state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
    failed = request(store, 'fictional-failure', 'update_account', {'accountId': account, 'type': 'invalid'})
    with pytest.raises(StoreError):
        store.execute_operation(failed)
    assert store.commit_evidence() == result.receipt.evidence
    store.close()
    store = Store(path)
    try:
        assert store.execute_operation(command).receipt == result.receipt
        with pytest.raises(StoreError):
            store.execute_operation(replace(command, operation_id='stale-fictional-request'))
        assert store.commit_evidence() == result.receipt.evidence
        validate_history(store.export_budget()['history'])
        before = store.commit_evidence()
        store.db.execute("UPDATE accounts SET name='External fictional edit' WHERE workspace='personal' AND id=?", (account,))
        assert store.commit_evidence().state_digest != before.state_digest
        assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
    finally:
        store.close()


@pytest.mark.parametrize('foreign_connection', [False, True])
def test_external_change_then_noop_then_other_edit_invalidates_every_hash_cache(tmp_path, foreign_connection):
    import sqlite3

    from whab_plugin_sqlite.portable import read_files

    from whab.domain.history import financial, state_digest

    path = tmp_path / 'external-cache.sqlite'
    store = Store(path, month='2026-11')
    try:
        account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
        ids = [store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
               'merchant': 'Fictional shop', 'amount': 100, 'note': 'Original'}) for _ in range(2)]
        def edit(index, note, identifier):
            return store.execute_operation(request(store, identifier, 'edit_transaction',
                    {'transactionId': ids[index], 'note': note}))
        edit(0, 'Warm cached state', 'warm')
        store.export_budget_files()
        if foreign_connection:
            with sqlite3.connect(path) as external:
                external.execute('UPDATE transactions SET note=? WHERE id=?', ('External note', ids[0]))
        else:
            store.db.execute('UPDATE transactions SET note=? WHERE id=?', ('External note', ids[0]))
        edit(1, 'Original', 'noop')
        edit(1, 'Later change', 'later')
        expected = store.export_budget('personal')
        assert expected['history']['entries'][-1]['afterDigest'] == state_digest(financial(expected), 2)
        assert read_files(store.export_budget_files()) == expected
        assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
        notes = {row['id']: row['note'] for row in expected['transactions']}
        assert notes == {ids[0]: 'External note', ids[1]: 'Later change'}
    finally:
        store.close()


def test_warm_legacy_generation_adopts_merkle_only_with_next_selected_commit(tmp_path):
    path = tmp_path / 'fictional-legacy.sqlite'
    store = Store(path, month='2026-10')
    try:
        account = store.add_account('personal', {'name': 'Fictional legacy cash', 'type': 'depository', 'balance': 0})
        metadata = store._authority_metadata()
        metadata['formatVersion'] = 2
        store._save_authority(metadata)
        before = store.commit_evidence()
        assert before.digest_version == 1
        if hasattr(store, '_native_merkle'):
            del store._native_merkle
        command = request(store, 'fictional-migrate-selected', 'classify_accounts', {'edits': [{'accountId': account, 'type': 'credit'}]})
        result = store.execute_operation(command)
        assert result.receipt.evidence.digest_version == 2
        assert result.receipt.evidence.generation == before.generation + 1
        assert result.receipt.evidence.state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
        record = read_storage_json(store.db.execute('SELECT record FROM storage_operations WHERE operation_id=?', (command.operation_id,)).fetchone()[0])
        record['evidence']['digest_version'] = 1
        store.db.execute('UPDATE storage_operations SET record=? WHERE operation_id=?', (canonical_storage_json(record), command.operation_id))
        with pytest.raises(StoreError, match='receipt evidence'):
            store.operation_receipt(command.operation_id)
    finally:
        store.close()


def test_first_audit_reuses_only_verified_equivalent_tree_and_keeps_reopen_proof(tmp_path, monkeypatch):
    from whab.domain.integrity import MerkleHasher

    legacy = Store(':memory:', month='2026-10', _authority=False)
    account = legacy.add_account('personal', {'name': 'Fictional legacy', 'type': 'depository', 'balance': 0})
    identifier = legacy.add_transaction('personal', {'accountId': account, 'date': '2026-10-01', 'merchant': 'Fictional shop', 'amount': 100})
    seed = legacy.export_budget()
    assert 'history' not in seed
    legacy.close()
    store = Store(tmp_path / 'fictional-first-history.sqlite', month='2026-10')
    try:
        store.restore_budget('personal', seed)
        used = []
        original = MerkleHasher.seed_array
        def capture(self, *args):
            result = original(self, *args)
            used.append(result)
            return result
        monkeypatch.setattr(MerkleHasher, 'seed_array', capture)
        command = request(store, 'fictional-first-audit', 'edit_transaction', {'transactionId': identifier, 'note': 'Retained note'})
        result = store.execute_operation(command)
        assert used == [True]
        assert result.receipt.evidence.state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
        journal = store.export_budget()['history']
        assert len(journal['entries']) == 1
        validate_history(journal)
        store.close()
        store = Store(tmp_path / 'fictional-first-history.sqlite')
        assert store.commit_evidence() == result.receipt.evidence
        assert store.execute_operation(command).replayed
    finally:
        store.close()
