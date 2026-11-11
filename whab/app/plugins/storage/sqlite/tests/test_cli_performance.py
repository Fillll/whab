"""Selective CLI reads and cached saves preserve complete durable state."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store, portable
from whab_plugin_sqlite.evidence import SnapshotEncoder

from whab.domain.history import digest
from whab.plugins import StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    encode_local_state,
    local_state_digest,
)


def execute(store, name, payload, identifier):
    evidence = store.commit_evidence()
    return store.execute_operation(StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
                                                   'personal', name, canonical_storage_json(payload)))


@pytest.fixture
def ledger(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-10')
    account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
    for i, merchant in enumerate(['Straße 100%', 'Fictional shop', 'Other shop']):
        store.add_transaction('personal', {'accountId': account, 'date': f'2026-10-0{i + 1}', 'merchant': merchant, 'amount': 100})
    yield store, account
    store.close()


def test_selective_reads_preserve_unicode_literal_matching_counts_and_metadata(ledger):
    store, account = ledger
    rows = store.transactions('personal')
    store.db.execute('UPDATE transactions SET pending=1 WHERE id=?', (rows[0]['id'],))
    store.db.execute('UPDATE transactions SET inferred_transfer=1 WHERE id=?', (rows[1]['id'],))
    selected = store.search_transactions('personal', {'merchant': ['STRASSE 100%'], 'since': '2026-10-01', 'until': '2026-10-01'})
    assert selected['complete'] and selected['total'] == 1
    assert selected['data'][0]['accountName'] == 'Fictional checking'
    assert selected['data'][0]['categoryName'] is None
    assert store.search_transactions('personal', {'merchant': ['100_']})['data'] == []
    assert store.search_transactions('personal', {'month': '2026-09'})['data'] == []
    report = store.search_transactions('personal', {'uncategorized': True, 'limit': 1})
    assert report['total'] == 3 and report['returned'] == 1 and report['pending'] == 1
    assert report['transferFlagged'] == 1 and report['postedNonTransfer'] == 1
    assert report['byCurrency'] == [{'currency': 'USD', 'count': 3}]
    report = store.search_transactions('personal', {'uncategorized': True, 'posted': True, 'excludeTransfers': True})
    assert report['total'] == report['returned'] == 1
    assert store.account_latest_dates('personal') == {account: '2026-10-03'}
    with pytest.raises(StoreError):
        store.search_transactions('absent', {})


def test_incremental_evidence_and_portable_images_match_full_rebuild_after_every_commit(ledger):
    store, account = ledger
    rows = store.transactions('personal')
    for i in range(4):
        execute(store, 'edit_transaction', {'transactionId': rows[i % 3]['id'], 'note': f'Fictional note {i}'}, f'edit-{i}')
        execute(store, 'update_account', {'accountId': account, 'type': 'credit' if i % 2 else 'depository', 'onBudget': True}, f'account-{i}')
        actual = store.commit_evidence()
        assert actual.state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
        image = store.export_budget_files()['budget.sqlite']
        loaded, _ = portable.read_files({'budget.sqlite': image}, with_receipts=True)
        assert loaded == store.export_budget('personal')
        assert store.export_budget_files()['budget.sqlite'] == image
    store.db.execute('UPDATE transactions SET note=? WHERE id=?', ('External SQL', rows[0]['id']))
    actual = store.commit_evidence()
    assert actual.state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
    # Rebuilding after an external write retains its changed row and valid history.
    assert store.export_budget_files()['budget.sqlite']


def test_evidence_cache_observes_other_connections_and_failed_writes(ledger):
    store, account = ledger
    before = store.commit_evidence()
    with sqlite3.connect(store.database_path) as other:
        other.execute('UPDATE accounts SET name=? WHERE id=?', ('Changed elsewhere', account))
    assert store.commit_evidence().state_digest != before.state_digest
    before = store.commit_evidence()
    store.db.execute("CREATE TRIGGER fail_update BEFORE UPDATE ON accounts BEGIN SELECT RAISE(ABORT,'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        execute(store, 'update_account', {'accountId': account, 'type': 'credit'}, 'failed-edit')
    assert store.commit_evidence() == before


def test_snapshot_encoder_is_canonical_and_bounded():
    row = {'id': 'Fictional 家庭', 'original': {'amount': 123}, 'note': '😀'}
    for ascii_only in (False, True):
        encoder = SnapshotEncoder(ensure_ascii=ascii_only)
        encoder.begin()
        value = {'transactions': [row], 'empty': {}, 'number': 42, 'flag': True}
        first = encoder.encode(value, limit=1000)
        if ascii_only:
            from hashlib import sha256
            assert sha256(first).hexdigest() == digest(value)
        else:
            assert first == canonical_storage_json(value)
        encoder.finish()
        encoder.begin()
        assert encoder.encode(value, limit=1000) == first
        with pytest.raises(StoreError):
            encoder.encode(value, limit=1)


def test_portable_deltas_roundtrip_after_bounded_edits_and_reject_unknown_bases(ledger):
    from hashlib import sha256

    from whab.budgets.file_changes import apply_file_changes
    store, account = ledger
    first = store.export_budget_file_changes('personal', {})
    assert first.keys() == {'files'}
    files = first['files']
    row = store.transactions('personal')[0]
    for i in range(3):
        execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': f'Delta {i}'}, f'delta-{i}')
        changes = store.export_budget_file_changes('personal', {key: sha256(raw).hexdigest() for key, raw in files.items()})
        files = apply_file_changes(files, changes)
        assert portable.read_files(files) == store.export_budget('personal')
    assert store.export_budget_file_changes('personal', {'budget.sqlite': 'wrong'}).keys() == {'files'}
    assert store.export_budget_file_changes('personal', [])['files'] == files


def test_long_audit_append_grows_reserved_space_and_reopens_ordinary_json(ledger):
    import json
    store, _ = ledger
    row = store.transactions('personal')[0]
    for i in range(90):
        execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': f'{i} ' + 'x' * 980}, f'growth-{i}')
    record, kind = store.db.execute("SELECT record,typeof(record) FROM budget_history WHERE workspace='personal'").fetchone()
    assert kind == 'text'
    assert len(json.loads(record)['entries']) >= 90
    assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
    assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')


def test_bounded_local_selection_refuses_oversized_complete_results(ledger):
    store, _ = ledger
    with pytest.raises(StoreError, match='16 MiB'):
        store.search_transactions('personal', {'maxBytes': 513})
    assert len(store.search_transactions('personal', {})['data']) == 3
