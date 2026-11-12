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


def test_empty_uncategorized_counts_and_serialized_selection_match(ledger):
    import json
    store, _ = ledger
    filters = {'uncategorized': True, 'merchant': ['No fictional match']}
    result = store.search_transactions('personal', filters)
    assert result['total'] == result['returned'] == 0
    assert result['bySource'] == result['byCurrency'] == result['data'] == []
    assert json.loads(store.search_transactions_json('personal', filters)) == result


def test_evidence_during_nested_native_scope_keeps_the_existing_journal(ledger):
    store, _ = ledger
    row = store.transactions('personal')[0]
    store.commit_evidence()
    with store._authority_write() as transaction:
        with store._authority_write(workspace='personal', transaction_ids=[row['id']]):
            store.db.execute('UPDATE transactions SET note=? WHERE id=?', ('Inside fictional transaction', row['id']))
            transaction.update(dirty=True, workspace='personal')
            assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))


def test_cached_transaction_chunks_handle_growth_scope_and_bounded_replacements():
    from copy import deepcopy
    encoder = SnapshotEncoder()
    value = {'workspaces': {'personal': {'transactions': [{'id': 'same-id', 'note': 'personal'}]},
                            'demo': {'transactions': [{'id': 'same-id', 'note': 'demo'}]}}}
    encoder.begin()
    assert encoder.encode(value, limit=10000) == canonical_storage_json(value)
    encoder.finish()
    updated = deepcopy(value)
    updated['workspaces']['personal']['transactions'].append({'id': 'second', 'note': 'new'})
    encoder.begin(transaction_ids=['second'])
    assert encoder.encode(updated, limit=10000) == canonical_storage_json(updated)
    encoder.finish()
    updated = deepcopy(updated)
    updated['workspaces']['personal']['transactions'][0]['note'] = 'changed'
    encoder.begin(transaction_ids=['same-id'])
    assert encoder.encode(updated, limit=10000) == canonical_storage_json(updated)
    encoder.finish()
    encoder.begin(transaction_ids=[])
    assert encoder.encode(updated, limit=10000) == canonical_storage_json(updated)
    assert encoder.encode({'transactions': None}, limit=10000) == canonical_storage_json({'transactions': None})


def test_portable_images_near_the_size_bound_drop_the_append_reserve(ledger, monkeypatch):
    store, _ = ledger
    original = store.export_budget_files()['budget.sqlite']
    row = store.transactions('personal')[0]
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Bounded export'}, 'bounded-export')
    monkeypatch.setattr(portable, 'MAX_DATABASE_BYTES', len(original) - 4096)
    image = store.export_budget_files()['budget.sqlite']
    assert len(image) <= len(original) - 4096
    assert portable.read_files({'budget.sqlite': image}) == store.export_budget('personal')


def test_first_saved_ledger_and_direct_export_remain_compatible_with_file_patches():
    from hashlib import sha256

    from whab.budgets.file_changes import apply_file_changes
    store = Store(':memory:', month='2026-10')
    try:
        files = store.export_budget_files()
        store.add_account('personal', {'name': 'Fictional first account', 'type': 'depository', 'balance': 1000})
        changes = store.export_budget_file_changes('personal', {name: sha256(raw).hexdigest() for name, raw in files.items()})
        files = apply_file_changes(files, changes)
        assert portable.read_files(files) == store.export_budget('personal')
        store.add_transaction('personal', {'accountId': store.accounts('personal')[0]['id'], 'date': '2026-10-01', 'merchant': 'Fictional', 'amount': 100})
        files = store.export_budget_files()
        changes = store.export_budget_file_changes('personal', {name: sha256(raw).hexdigest() for name, raw in files.items()})
        assert apply_file_changes(files, changes) == files
        assert store.search_transactions('personal', {'maxBytes': 1000000})['total'] == 1
    finally:
        store.close()


def test_large_positive_sample_limits_keep_all_matches(ledger):
    store, _ = ledger
    result = store.search_transactions('personal', {'uncategorized': True, 'limit': 10**100})
    assert result['returned'] == result['total'] == 3


def test_canonical_transaction_chunks_keep_separators_and_selected_replacements():
    encoder = SnapshotEncoder()
    rows = [{'id': str(i), 'note': 'Fictional 家庭'} for i in range(1100)]
    for selection in (None, ['512'], []):
        encoder.begin(transaction_ids=selection)
        if selection:
            rows[512] = {**rows[512], 'note': 'Changed'}
        value = {'transactions': rows, 'empty': [], 'flags': [True, False]}
        assert b''.join(encoder.chunks(value, limit=100000)) == canonical_storage_json(value)
        encoder.finish()


def test_incremental_personal_and_demo_exports_keep_separate_images_and_audit_offsets(ledger):
    store, _ = ledger
    for workspace in ('personal', 'demo'):
        store.export_budget_files(workspace)
    for index, workspace in enumerate(('demo', 'personal', 'demo', 'personal')):
        row = store.transactions(workspace)[0]
        store.edit_transaction(workspace, row['id'], note=f'Isolated {workspace} {index}')
        files = store.export_budget_files(workspace)
        assert portable.read_files(files) == store.export_budget(workspace)


def test_cached_native_views_include_details_balances_protected_fields_and_noop(ledger):
    store, account = ledger
    store.db.execute('UPDATE accounts SET mask=? WHERE id=?', ('9012', account))
    row = store.transactions('personal')[0]
    for index, changes in enumerate(({'merchant': 'Fictional 家庭', 'amount': 150}, {'merchant': 'Fictional 家庭', 'amount': 150}, {'date': '2026-09-30'})):
        execute(store, 'edit_transaction_details', {'transactionId': row['id'], 'changes': changes}, f'details-{index}')
        assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
    assert store._native_records[1]['workspaces']['personal']['associations'][0]['mask'] == '9012'


def test_unknown_bounded_native_command_rebuilds_protected_evidence(ledger):
    store, account = ledger
    row = store.transactions('personal')[0]
    current = store.commit_evidence()
    with store._authority_write(workspace='personal', transaction_ids=[row['id']]) as transaction:
        store.db.execute('UPDATE accounts SET mask=? WHERE id=?', ('4821', account))
        store.db.execute('UPDATE transactions SET note=? WHERE id=?', ('Custom native command', row['id']))
        transaction.update(dirty=True, workspace='personal', result=None,
                           operation=StorageOperation('custom-bounded', current.authority_epoch, current.generation,
                                                      'personal', 'edit_transaction', canonical_storage_json({'transactionId': row['id'], 'note': 'Custom native command'})))
    assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))


def test_merchant_index_preserves_literals_and_does_not_change_commit_evidence(ledger):
    store, account = ledger
    for merchant in ('Fictional "quote" 100%_', 'CAFÉ 家庭👩‍💻', 'Straße ﬃ', 'Other'):
        store.add_transaction('personal', {'accountId': account, 'date': '2026-10-05', 'merchant': merchant, 'amount': 100})
    rows = store.transactions('personal')
    evidence = store.commit_evidence()
    changes = store.db.total_changes
    for needle in ('STRASSE', 'ffi', 'CAFÉ', '家庭', '👩‍💻', '"quote"', '100%_', '"qu', 'x', 'No match'):
        result = store.search_transactions('personal', {'merchant': [needle]})
        assert {row['id'] for row in result['data']} == {row['id'] for row in rows if needle.casefold() in row['merchant'].casefold()}
    assert store.db.total_changes == changes and store.commit_evidence() == evidence
    assert not store.db.execute("SELECT 1 FROM sqlite_schema WHERE name IN ('merchants','terms')").fetchall()


def test_merchant_index_tracks_native_commits_and_external_connections(ledger):
    store, account = ledger
    row = store.transactions('personal')[0]
    store.search_transactions('personal', {'merchant': ['shop']})
    cached = store._merchant_index
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Annotation only'}, 'indexed-note')
    execute(store, 'update_account', {'accountId': account, 'type': 'credit'}, 'indexed-account')
    assert store._merchant_index is cached
    execute(store, 'edit_transaction_details', {'transactionId': row['id'], 'changes': {'merchant': 'New native merchant'}}, 'indexed-merchant')
    assert store._merchant_index is cached
    assert store.search_transactions('personal', {'merchant': ['New native']})['data'][0]['id'] == row['id']
    with sqlite3.connect(store.database_path) as other:
        other.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Changed externally', row['id']))
    assert store.search_transactions('personal', {'merchant': ['externally']})['data'][0]['id'] == row['id']
    assert store._merchant_index is not cached
    cached = store._merchant_index
    store.db.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Same connection', row['id']))
    assert store.search_transactions('personal', {'merchant': ['Same connection']})['total'] == 1
    assert store._merchant_index is not cached


def test_index_failure_after_commit_cannot_fail_or_lose_the_financial_edit(ledger):
    store, _ = ledger
    row = store.transactions('personal')[0]
    store.search_transactions('personal', {'merchant': ['shop']})
    store._merchant_index.close()
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Durable despite index failure'}, 'index-failure')
    assert store._merchant_index is None
    assert next(tx for tx in store.transactions('personal') if tx['id'] == row['id'])['note'] == 'Durable despite index failure'
    assert store.search_transactions('personal', {'merchant': ['shop']})['total'] == 2


def test_merchant_index_without_fts_keeps_complete_unicode_results(monkeypatch):
    from whab_plugin_sqlite import merchant_index
    class NoFTS(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE VIRTUAL TABLE'):
                raise sqlite3.OperationalError('no such module: fts5')
            return super().execute(sql, *args)
    monkeypatch.setattr(merchant_index, 'connect', lambda *a, **kw: sqlite3.connect(*a, factory=NoFTS, **kw))
    cached = merchant_index.MerchantIndex([('personal', 'one', 'Straße'), ('demo', 'two', 'Straße')], (0, 0))
    try:
        assert cached.identifiers('personal', ['STRASSE']) == ['one']
        cached.update('personal', ['one', 'three'], [('three', 'New 家庭')])
        assert cached.identifiers('personal', ['Straße']) == []
        assert cached.identifiers('personal', ['家庭']) == ['three']
    finally:
        cached.close()


def test_merchant_index_initialization_propagates_other_sqlite_failures(monkeypatch):
    from whab_plugin_sqlite import merchant_index
    class Broken(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith('CREATE VIRTUAL TABLE'):
                raise sqlite3.OperationalError('Fictional index initialization failure')
            return super().execute(sql, *args)
    monkeypatch.setattr(merchant_index, 'connect', lambda *a, **kw: sqlite3.connect(*a, factory=Broken, **kw))
    with pytest.raises(sqlite3.OperationalError, match='Fictional index'):
        merchant_index.MerchantIndex([], (0, 0))


def test_merchant_index_updates_and_deletes_external_content_rows():

    from whab_plugin_sqlite.merchant_index import MerchantIndex
    cached = MerchantIndex([('personal', 'one', 'Fictional shop')], (0, 0))
    try:
        cached.update('personal', ['one', 'two'], [('one', 'Changed merchant'), ('two', 'Added merchant')])
        assert set(cached.identifiers('personal', ['merchant'])) == {'one', 'two'}
        cached.update('personal', ['one'], [])
        assert cached.identifiers('personal', ['Changed']) == []
        assert cached.identifiers('personal', ['Added']) == ['two']
        cached.update('personal', ['absent'], [])
        assert cached.identifiers('personal', ['Added']) == ['two']
    finally:
        cached.close()


def test_unknown_native_identity_changes_fall_back_to_complete_evidence(ledger):
    store, _ = ledger
    row = store.transactions('personal')[0]
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Prime native heads'}, 'prime-identities')
    for selected in ([row['id']], ['missing-before']):
        with store._authority_write(workspace='personal', transaction_ids=selected) as transaction:
            if selected == [row['id']]:
                store.db.execute('DELETE FROM transactions WHERE id=?', (row['id'],))
            else:
                store.db.execute("INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) VALUES(?,?,?,?,?,?,?)",
                                 ('missing-before', 'personal', row['accountId'], '2026-10-05', 'Fictional insert', 100, 'manual'))
            transaction.update(dirty=True, workspace='personal')
        assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))


def test_external_commit_between_native_commit_and_cache_publication_invalidates_indexes(ledger):
    store, _ = ledger
    row = store.transactions('personal')[0]
    store.search_transactions('personal', {'merchant': ['shop']})
    cached = store._merchant_index
    store.export_budget_files()
    original = store.db
    class ExternalCommit:
        armed = True
        def __getattr__(self, name):
            return getattr(original, name)
        def execute(self, sql, *args):
            result = original.execute(sql, *args)
            if sql == 'COMMIT' and self.armed and store._writing is not None and store._writing.get('dirty'):
                self.armed = False
                with sqlite3.connect(store.database_path) as other:
                    other.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Concurrent fictional merchant', row['id']))
            return result
    store.db = ExternalCommit()
    try:
        execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Native edit'}, 'concurrent-publication')
        assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
        assert store.search_transactions('personal', {'merchant': ['Concurrent fictional']})['data'][0]['id'] == row['id']
        assert store._merchant_index is not cached
        assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')
    finally:
        store.db = original


def test_unbounded_and_untracked_changes_rebuild_the_merchant_index(ledger):
    store, account = ledger
    row = store.transactions('personal')[0]
    store.search_transactions('personal', {'merchant': ['shop']})
    cached = store._merchant_index
    store.add_transaction('personal', {'accountId': account, 'date': '2026-10-05', 'merchant': 'Unbounded new merchant', 'amount': 100})
    assert store.search_transactions('personal', {'merchant': ['Unbounded new']})['total'] == 1
    assert store._merchant_index is not cached
    cached = store._merchant_index
    store.db.execute('UPDATE transactions SET merchant=? WHERE id=?', ('Untracked merchant', row['id']))
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Native edit after untracked write'}, 'untracked-edit')
    assert store.search_transactions('personal', {'merchant': ['Untracked']})['total'] == 1
    assert store._merchant_index is not cached


def test_unchanged_legacy_annotation_without_history_keeps_exact_native_evidence(ledger):
    store, _ = ledger
    row = store.transactions('personal')[0]
    store.db.execute("DELETE FROM budget_history WHERE workspace='personal'")
    before = store.commit_evidence()
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': ''}, 'legacy-unchanged')
    assert store.commit_evidence().state_digest == before.state_digest
    assert store.commit_evidence().state_digest == local_state_digest(*encode_local_state(store._local_states(), validate=False))
    assert not store.db.execute("SELECT 1 FROM budget_history WHERE workspace='personal'").fetchall()


def test_trigram_candidates_match_python_casefold_for_unicode_substrings():
    import random

    from whab_plugin_sqlite.merchant_index import MerchantIndex
    rng = random.Random(184)
    alphabet = 'aAßﬃé́家庭👩‍💻" %_[]:*+-'
    rows = [('personal' if i % 2 else 'demo', str(i), ''.join(rng.choices(alphabet, k=24))) for i in range(100)]
    cached = MerchantIndex(rows, (0, 0))
    try:
        for _, _, merchant in rows:
            start = rng.randrange(10)
            for width in (1, 2, 3, 6):
                needle = merchant[start:start + width]
                actual = set(cached.identifiers('personal', [needle]))
                expected = {identifier for workspace, identifier, text in rows if workspace == 'personal' and needle.casefold() in text.casefold()}
                assert actual == expected
    finally:
        cached.close()


def test_sparse_uncategorized_samples_do_not_scan_categorized_history():
    store = Store(':memory:', month='2026-10', _authority=False)
    try:
        account = store.add_account('personal', {'name': 'Fictional', 'type': 'depository', 'balance': 10000})
        store.add_transaction('personal', {'accountId': account, 'date': '2025-01-01', 'merchant': 'Only uncategorized', 'amount': 100})
        store.db.executemany("INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source,category_id) VALUES(?,'personal',?,'2026-10-01','Already categorized',100,'manual','groceries')",
                             [(f'categorized-{i}', account) for i in range(5000)])
        steps = []
        store.db.set_progress_handler(lambda: steps.append(1) or 0, 100)
        result = store.search_transactions('personal', {'uncategorized': True, 'limit': 1})
        store.db.set_progress_handler(None, 0)
        assert result['total'] == result['returned'] == 1
        assert result['data'][0]['merchant'] == 'Only uncategorized'
        assert len(steps) < 50, 'A limited sample must avoid walking categorized rows'
        store.search_transactions('personal', {'merchant': ['Only uncategorized']})
        steps.clear()
        store.db.set_progress_handler(lambda: steps.append(1) or 0, 100)
        result = store.search_transactions('personal', {'merchant': ['Only uncategorized']})
        store.db.set_progress_handler(None, 0)
        assert result['total'] == 1
        assert result['data'][0]['merchant'] == 'Only uncategorized'
        assert len(steps) < 50, 'A selective merchant query must avoid walking unrelated rows'
        broad = store.search_transactions('personal', {'merchant': ['Already categorized']})
        assert broad['total'] == len(broad['data']) == 5000
        sample = store.search_transactions('personal', {'merchant': ['Only uncategorized'], 'uncategorized': True, 'limit': 1})
        assert sample['total'] == sample['returned'] == 1
    finally:
        store.close()


def test_optional_runtime_index_definitions_are_checked_without_schema_combinations(ledger):
    store, _ = ledger
    image = store.export_budget_files()['budget.sqlite']
    sql = store.db.execute("SELECT sql FROM sqlite_schema WHERE name='tx_uncategorized_recent'").fetchone()[0]
    clean = sqlite3.connect(':memory:')
    try:
        clean.deserialize(image)
        clean.execute(sql)
        assert portable.read_files({'budget.sqlite': clean.serialize()}) == store.export_budget('personal')
        clean.execute('DROP INDEX tx_uncategorized_recent')
        clean.execute('CREATE INDEX tx_uncategorized_recent ON transactions(merchant)')
        with pytest.raises(StoreError, match='unsupported database schema'):
            portable.read_files({'budget.sqlite': clean.serialize()})
    finally:
        clean.close()


def test_uncategorized_sample_keeps_read_compatibility_without_the_new_runtime_index(ledger):
    store, _ = ledger
    expected = store.search_transactions('personal', {'uncategorized': True, 'limit': 1})
    store.db.execute('DROP INDEX tx_uncategorized_recent')
    assert store.search_transactions('personal', {'uncategorized': True, 'limit': 1}) == expected


def test_failed_optional_image_preparation_falls_back_without_affecting_commit(ledger, monkeypatch):
    store, _ = ledger
    store.export_budget_files()
    row = store.transactions('personal')[0]
    def failed(*args):
        raise RuntimeError('Fictional preparation failure')
    with monkeypatch.context() as patch:
        patch.setattr(portable, '_prepared_delta', failed)
        execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Still durable'}, 'failed-preparation')
    assert store.operation_receipt('failed-preparation') is not None
    assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')


def test_prepared_image_is_not_promoted_when_native_commit_rolls_back(ledger, monkeypatch):
    store, _ = ledger
    old = store.export_budget_files()
    cached = store._portable_images['personal']
    row = store.transactions('personal')[0]
    def fail(*args, **kwargs):
        raise sqlite3.OperationalError('Fictional metadata failure')
    with monkeypatch.context() as patch:
        patch.setattr(store, '_save_authority', fail)
        with pytest.raises(sqlite3.OperationalError, match='Fictional metadata failure'):
            execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Rolled back'}, 'rolled-back-preparation')
    assert store._portable_images['personal'] is cached
    assert store.operation_receipt('rolled-back-preparation') is None
    assert portable.read_files(old) == store.export_budget('personal')
    assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')
    other = next(item for item in store.transactions('personal') if item['id'] != row['id'])
    execute(store, 'edit_transaction', {'transactionId': other['id'], 'note': 'After rollback'}, 'after-rolled-back-preparation')
    assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')


def test_preparation_discards_unchanged_and_oversized_images(ledger, monkeypatch):
    store, _ = ledger
    row = store.transactions('personal')[0]
    store.export_budget_files()
    execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': ''}, 'unchanged-preparation')
    assert getattr(store, '_prepared_portable_changes', None) is None
    store.export_budget_files()
    with monkeypatch.context() as patch:
        patch.setattr(portable, 'MAX_DATABASE_BYTES', 1)
        execute(store, 'edit_transaction', {'transactionId': row['id'], 'note': 'Bounded preparation'}, 'oversized-preparation')
    assert getattr(store, '_prepared_portable_changes', None) is None
    assert portable.read_files(store.export_budget_files()) == store.export_budget('personal')
