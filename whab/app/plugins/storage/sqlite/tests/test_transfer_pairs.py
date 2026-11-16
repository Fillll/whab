"""Native transfer batches commit all legs once and preserve restart receipts."""

import sqlite3
from contextlib import closing
from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JSONStore
from whab_plugin_sqlite import Store as SQLiteStore

from whab.domain.accounts import normalize_account
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def fictional_state(count=2):
    state = initial_state('personal', '2026-10')
    state['accounts'] = {
        key: normalize_account({'id': key, 'name': name, 'institution': '', 'type': 'depository',
            'balance': 100000, 'currency': 'USD', 'source': 'manual', 'stale': False,
            'balanceUnknown': False, 'onBudget': key == 'a', 'closed': key == 'b'})
        for key, name in [('a', 'Fictional current'), ('b', 'Исторический архив')]
    }
    state['transactions'] = {}
    for index in range(count):
        for direction, account, amount in [('out', 'a', index + 1), ('in', 'b', -index - 1)]:
            key = f'{direction}-{index}'
            state['transactions'][key] = {
                'id': key, 'accountId': account, 'date': '2026-10-31',
                'merchant': '  Fictional transfer 商店  ', 'merchantLogo': None,
                'amount': amount, 'categoryId': None, 'pending': False, 'reviewed': False,
                'source': 'ynab', 'note': '[YNAB: uncleared]\nExact note  ',
                'isTransfer': True, 'inferredTransfer': True, 'categoryEdited': True,
                'deleted': False, 'categoryAgent': 'Prior agent',
                'original': {'merchant': '  Imported merchant  '},
            }
    return state


@pytest.fixture(params=[SQLiteStore, JSONStore], ids=['sqlite', 'json'])
def transfer_store(request, tmp_path):
    path = tmp_path / ('fictional.sqlite' if request.param is SQLiteStore else 'fictional.json')
    store = request.param(path, month='2026-11')
    store.restore_budget('personal', portable_snapshot(fictional_state()))
    yield store, path, request.param
    store.close()


def recorded(store, name, parameters, request_id=None):
    evidence = store.commit_evidence()
    return StorageOperation(request_id or uuid4().hex, evidence.authority_epoch, evidence.generation,
        'personal', 'recorded_command', canonical_storage_json({
            'operation': name, 'parameters': parameters,
            'context': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-transfer-batch',
                        'operation': 'match_transfers' if name == 'apply_transfer_matches' else name},
        }))


def portable_version(files):
    import json

    if 'budget.json' in files:
        return json.loads(files['budget.json'])['formatVersion']
    from whab_plugin_sqlite.portable_journal import MAGIC

    if files['budget.sqlite'].startswith(MAGIC):
        return 4
    with closing(sqlite3.connect(':memory:')) as image:
        image.deserialize(files['budget.sqlite'])
        return image.execute('PRAGMA user_version').fetchone()[0]


def test_portable_transfer_fence_preserves_legacy_and_fences_first_bounded_save(transfer_store, tmp_path, monkeypatch):
    store, path, Store = transfer_store
    store.edit_transaction('personal', 'out-0', note='Fictional warm history')
    legacy = store.export_budget_files()
    assert portable_version(legacy) == (4 if Store is SQLiteStore else 1)
    restored = Store(tmp_path / ('legacy-restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', legacy)
        assert restored.export_budget('personal')['transactions'] == store.export_budget('personal')['transactions']
        assert portable_version(restored.export_budget_files()) == (4 if Store is SQLiteStore else 1)
        if Store is SQLiteStore:
            from whab_plugin_sqlite.portable import read_files
            from whab_plugin_sqlite.portable_journal import read
            raw = {'budget.sqlite': read(legacy['budget.sqlite'])[0]}
            assert portable_version(raw) == 1
            assert read_files(raw) == restored.export_budget('personal')
    finally:
        restored.close()
    with monkeypatch.context() as bounded:
        if Store is SQLiteStore:
            bounded.setattr(store, 'export_budget', lambda *_: pytest.fail('First paired save must use the existing bounded image delta.'))
        store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])
        current = store.export_budget_files()
    assert portable_version(current) == (4 if Store is SQLiteStore else 2)
    assert portable_version(legacy) == (4 if Store is SQLiteStore else 1)
    restored = Store(tmp_path / ('paired-restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', current)
        assert restored.export_budget('personal')['transactions'] == store.export_budget('personal')['transactions']
        assert portable_version(restored.export_budget_files()) == (4 if Store is SQLiteStore else 2)
        if Store is SQLiteStore:
            raw = {'budget.sqlite': read(restored.export_budget_files()['budget.sqlite'])[0]}
            assert portable_version(raw) == 2
            assert read_files(raw) == restored.export_budget('personal')
    finally:
        restored.close()


def test_native_batch_replay_reopen_portable_and_reversible_history(transfer_store, tmp_path):
    store, path, Store = transfer_store
    assert store.supports_bulk_transfer_matching() and store.supports_transfer_integrity()
    pairs = [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}]
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    preview = store.preview_transfer_matches('personal', pairs, reviewed=True)
    assert store.export_budget('personal') == before
    assert preview['pairCount'] == 2 and preview['count'] == 4
    assert all(pair['crossesBudgetScope'] and pair['archivedAccounts'] == ['b'] for pair in preview['pairs'])
    operation = recorded(store, 'apply_transfer_matches', {
        'pairs': pairs, 'reviewed': True, 'fingerprint': preview['fingerprint'],
    })
    result = store.execute_operation(operation)
    assert store.commit_evidence().generation == evidence.generation + 1
    with pytest.raises(StoreError):
        store.execute_operation(replace(operation, operation_id=uuid4().hex))
    with pytest.raises(StoreError):
        store.execute_operation(replace(operation, payload=canonical_storage_json({
            'operation': 'apply_transfer_matches', 'parameters': {'pairs': pairs, 'reviewed': False,
                'fingerprint': preview['fingerprint']},
            'context': {'actor': 'Codex', 'actorKind': 'agent'},
        })))
    saved = store.export_budget('personal')
    assert saved['accounts'] == before['accounts']
    assert saved['assignments'] == before['assignments']
    assert len(saved['transactions']) == 4
    for old, row in zip(before['transactions'], saved['transactions']):
        assert row['reviewed'] and row['transferPeerId']
        assert {k: v for k, v in row.items() if k not in {'transferPeerId', 'isTransfer', 'reviewed'}} == {
            k: v for k, v in old.items() if k not in {'isTransfer', 'reviewed'}}
    assert store.execute_operation(operation).receipt == result.receipt
    assert store.export_budget('personal') == saved
    assert store.commit_evidence().generation == evidence.generation + 1
    files = store.export_budget_files()
    restored = Store(tmp_path / ('restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        assert restored.export_budget('personal')['transactions'] == saved['transactions']
    finally:
        restored.close()
    store.close()
    reopened = Store(path, month='2026-11')
    try:
        assert reopened.execute_operation(operation).receipt == result.receipt
        history = reopened.budget_history('personal')['entries']
        entry = next(row for row in history if row['operation'] == 'match_transfers')
        assert entry['actor'] == 'Codex' and entry['batchId'] == 'fictional-transfer-batch'
        assert entry['reversible'] and entry['changeCount'] == 4
        for action in ('undo', 'redo'):
            change = reopened.history_preview('personal', action, entry['id'])
            reopened.history_apply('personal', action, entry['id'], change['fingerprint'])
        assert {row['id']: row for row in reopened.export_budget('personal')['transactions']} == {
            row['id']: row for row in saved['transactions']}
    finally:
        reopened.close()


@pytest.mark.parametrize('Store', [SQLiteStore, JSONStore], ids=['sqlite', 'json'])
def test_mixed_existing_zero_pairs_are_atomic_reviewed_replayable_and_portable(tmp_path, Store):
    path = tmp_path / 'fictional-zero-pairs'
    store = Store(path, month='2026-11')
    try:
        state = fictional_state(2)
        for identifier in ('out-0', 'in-0'):
            state['transactions'][identifier]['amount'] = 0
        store.restore_budget('personal', portable_snapshot(state))
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        with pytest.raises(StoreError):
            store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-1'}], reviewed=True)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        pairs = [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}]
        preview = store.preview_transfer_matches('personal', pairs, reviewed=True)
        operation = recorded(store, 'apply_transfer_matches', {
            'pairs': pairs, 'reviewed': True, 'fingerprint': preview['fingerprint'],
        })
        result = store.execute_operation(operation)
        assert result.receipt.evidence.generation == evidence.generation + 1
        assert store.execute_operation(operation).receipt == result.receipt
        saved = store.export_budget('personal')
        assert len(saved['transactions']) == len(before['transactions']) == 4
        assert saved['accounts'] == before['accounts'] and saved['assignments'] == before['assignments']
        for previous, row in zip(before['transactions'], saved['transactions']):
            assert row['reviewed'] and row['transferPeerId']
            assert {key: value for key, value in row.items() if key not in {'reviewed', 'transferPeerId'}} == {
                key: value for key, value in previous.items() if key != 'reviewed'}
        files = store.export_budget_files()
        assert portable_version(files) == (4 if Store is SQLiteStore else 2)
    finally:
        store.close()
    reopened = Store(path, month='2026-11')
    restored = Store(tmp_path / 'fictional-zero-restored', month='2026-11')
    try:
        assert reopened.execute_operation(operation).receipt == result.receipt
        assert reopened.commit_evidence() == result.receipt.evidence
        restored.restore_budget_files('personal', files)
        assert restored.export_budget('personal')['transactions'] == saved['transactions']
        assert all(row['transferPairValid'] and row['reviewed'] for row in restored.transactions('personal'))
        assert restored.transfer_audit('personal', {})['total'] == 0
        entry = next(row for row in reopened.budget_history('personal')['entries'] if row['operation'] == 'match_transfers')
        assert entry['changeCount'] == 4 and entry['actor'] == 'Codex'
        for action in ('undo', 'redo'):
            plan = reopened.history_preview('personal', action, entry['id'])
            reopened.history_apply('personal', action, entry['id'], plan['fingerprint'])
        assert {row['id']: row for row in reopened.export_budget('personal')['transactions']} == {
            row['id']: row for row in saved['transactions']}
    finally:
        reopened.close()
        restored.close()


@pytest.mark.parametrize('pairs', [
    [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'missing'}],
    [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-0'}],
    [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'in-1', 'inId': 'out-1'}],
    [{'outId': 'out-0', 'inId': 'in-1'}],
])
def test_whole_batch_failures_preserve_ledger_history_generation(transfer_store, pairs):
    store, _, _ = transfer_store
    before, evidence = store.export_budget('personal'), store.commit_evidence()
    with pytest.raises(StoreError):
        store.apply_transfer_matches('personal', pairs, reviewed=True)
    assert store.export_budget('personal') == before
    assert store.commit_evidence() == evidence


def test_sqlite_100_pairs_use_one_commit_and_failure_rolls_back_everything(tmp_path):
    store = SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state(100)))
        pairs = [{'outId': f'out-{i}', 'inId': f'in-{i}'} for i in range(100)]
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        preview = store.preview_transfer_matches('personal', pairs)
        operation = recorded(store, 'apply_transfer_matches', {'pairs': pairs,
            'reviewed': False, 'fingerprint': preview['fingerprint']})
        store.db.execute("CREATE TRIGGER fail_transfer BEFORE UPDATE OF transfer_peer_id ON transactions "
            "WHEN NEW.id='in-99' BEGIN SELECT RAISE(ABORT,'fictional transfer fault'); END")
        with pytest.raises(sqlite3.IntegrityError, match='fictional transfer fault'):
            store.execute_operation(operation)
        assert store.export_budget('personal') == before
        assert store.commit_evidence() == evidence
        assert store.operation_receipt(operation.operation_id) is None
        store.db.execute('DROP TRIGGER fail_transfer')
        statements = []
        store.db.set_trace_callback(statements.append)
        store.execute_operation(operation)
        store.db.set_trace_callback(None)
        assert statements.count('COMMIT') == 1
        assert store.commit_evidence().generation == evidence.generation + 1
        assert len(store.transfer_audit('personal', {})['issues']) == 0
    finally:
        store.close()


def test_two_hundred_selected_pair_legs_keep_incremental_roots_and_complete_portable_replay(tmp_path, monkeypatch):
    from whab_plugin_sqlite.portable import read_files
    from whab_plugin_sqlite.portable_journal import MAGIC, read

    from whab.plugins.storage_transfer import encode_local_state, local_state_digest

    store = SQLiteStore(tmp_path / 'fictional-pair-roots.sqlite', month='2026-11')
    target = JSONStore(tmp_path / 'fictional-pair-copy.json', month='2026-11', initial_role='staged')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state(100)))
        store.edit_transaction('personal', 'out-0', note='Warm the complete verified state')
        original = store.export_budget_files()['budget.sqlite']
        pairs = [{'outId': f'out-{i}', 'inId': f'in-{i}'} for i in range(100)]
        with monkeypatch.context() as patch:
            patch.setattr(store, '_local_states', lambda: pytest.fail('Matching selected legs must reuse unrelated verified records'))
            patch.setattr(store, 'export_budget', lambda *_: pytest.fail('Matching selected legs must append a portable frame'))
            store.apply_transfer_matches('personal', pairs)
            saved = store.export_budget_files()['budget.sqlite']
        assert saved.startswith(MAGIC) and saved.startswith(original)
        assert len(read(saved)[1]) == 1 and read(saved)[1][0]['operation'] == 'match_transfers'
        expected = store.export_budget('personal')
        assert read_files({'budget.sqlite': saved}) == expected
        evidence = store.commit_evidence()
        assert evidence.digest_version == 2
        assert evidence.state_digest == local_state_digest(*encode_local_state(store._local_states()), version=2)
        target.import_local('fictional-pair-copy', store.export_local())
        assert target.commit_evidence().state_digest == evidence.state_digest
        assert target.commit_evidence().digest_version == 2
        target.close()
        target = JSONStore(tmp_path / 'fictional-pair-copy.json')
        assert target.commit_evidence().state_digest == evidence.state_digest
        assert all(row['transferPairValid'] for row in target.transactions('personal'))
    finally:
        store.close()
        target.close()


def test_sqlite_incoming_third_leg_is_seen_by_preview_and_single_review(tmp_path):
    store = SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        store.db.execute("UPDATE transactions SET transfer_peer_id='out-0' WHERE workspace='personal' AND id='in-1'")
        before = deepcopy(store.state('personal'))
        for call in (
            lambda: store.preview_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}]),
            lambda: store.preview_transfer_match('personal', ['out-0', 'in-0'], 'match'),
        ):
            with pytest.raises(StoreError):
                call()
        assert store.state('personal') == before
    finally:
        store.close()


def test_unreviewed_pair_can_be_reviewed_by_each_existing_annotation_path(transfer_store):
    store, _, _ = transfer_store
    store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}])
    store.edit_transaction('personal', 'out-0', reviewed=True)
    store.review_transactions('personal', ['in-0'], True)
    store.edit_transactions('personal', [{'transactionId': 'out-1', 'reviewed': True}])
    result = {row['id']: row for row in store.transactions('personal')}
    assert all(result[key]['reviewed'] for key in ('out-0', 'in-0', 'out-1'))
    assert not result['in-1']['reviewed']
    assert all(row['transferPairValid'] for row in result.values())


def test_bulk_account_scope_guard_and_annotation_preview_preserve_transfer_context(transfer_store):
    store, _, _ = transfer_store
    store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}], reviewed=True)
    before = store.export_budget('personal')
    edits = [{'accountId': 'a', 'onBudget': False}]
    with pytest.raises(StoreError, match='Unmatch confirmed transfers'):
        store.preview_bulk_edits('personal', 'classify_accounts', edits)
    with pytest.raises(StoreError, match='Unmatch confirmed transfers'):
        store.classify_accounts('personal', edits)
    assert store.export_budget('personal') == before
    note_edits = [{'transactionId': 'out-0', 'appendNote': 'Fictional note'}]
    preview = store.preview_bulk_edits('personal', 'edit_transactions', note_edits)
    assert preview['changes'][0]['after']['note'].endswith('\nFictional note')
    assert store.export_budget('personal') == before


def test_sqlite_paged_and_filtered_reads_prove_peer_outside_selection(tmp_path):
    store = SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])
        page = store.transaction_register_page('personal', None, None, 'USD', ('a',), '', 'all', 1, 100)
        rows = {row['id']: row for row in page['transactions']}
        assert rows['out-0']['transferPairValid']
        assert 'in-0' not in rows
        filtered = store.transaction_register_filtered('personal', None, None, 'USD', ('a',))
        assert next(row for row in filtered['transactions'] if row['id'] == 'out-0')['transferPairValid']
        store.db.execute("UPDATE transactions SET transfer_peer_id='out-0' WHERE workspace='personal' AND id='in-1'")
        page = store.transaction_register_page('personal', None, None, 'USD', ('a',), '', 'all', 1, 100)
        assert not next(row for row in page['transactions'] if row['id'] == 'out-0')['transferPairValid']
        with pytest.raises(StoreError):
            store.edit_transaction('personal', 'out-0', reviewed=True)
    finally:
        store.close()


def test_sqlite_search_byte_budget_includes_transfer_graph_proof(tmp_path):
    import json

    store = SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state(1)))
        store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])
        filters = {'limit': 1, 'maxBytes': 9999}
        result = store.search_transactions('personal', filters)
        row = result['data'][0]
        assert row['transferPairValid'] is True
        unproven = {key: value for key, value in row.items() if key != 'transferPairValid'}
        # The existing response allowance plus the raw row fits exactly. Its
        # subsequently verified graph metadata must still respect the same cap.
        bounded = {**filters, 'maxBytes': 999}
        budget = 512 + len(json.dumps(bounded, ensure_ascii=False).encode()) + len(
            json.dumps(unproven, ensure_ascii=False, separators=(',', ':')).encode(),
        ) + 1
        assert len(str(budget)) == len(str(bounded['maxBytes']))
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        with pytest.raises(StoreError, match='Restrict dates or merchant selection'):
            store.search_transactions('personal', {**bounded, 'maxBytes': budget})
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        assert store.search_transactions('personal', filters) == result
    finally:
        store.close()


def test_sqlite_related_peer_context_uses_indexes_and_keeps_portable_schema_compatible(tmp_path):
    store = SQLiteStore(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        statements = []
        store.db.set_trace_callback(statements.append)
        store.preview_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])
        store.db.set_trace_callback(None)
        queries = [sql for sql in statements if sql.startswith('SELECT id,transfer_peer_id FROM transactions')]
        assert queries
        for query in queries:
            plans = [row['detail'] for row in store.db.execute('EXPLAIN QUERY PLAN ' + query)]
            assert not any('SCAN transactions' in plan for plan in plans)
            assert any('tx_transfer_peer' in plan for plan in plans)
        with closing(sqlite3.connect(':memory:')) as image:
            from whab_plugin_sqlite.portable_journal import read
            image.deserialize(read(store.export_budget_files()['budget.sqlite'])[0])
            assert image.execute("SELECT 1 FROM sqlite_schema WHERE name='tx_transfer_peer'").fetchone() is None
        # Previous native files can be opened read-only before an additive index
        # is installed. The query remains correct without an INDEXED BY mandate.
        store.db.execute('DROP INDEX tx_transfer_peer')
        readonly = SQLiteStore(store.database_path, read_only=True)
        try:
            assert readonly.preview_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])['pairCount'] == 1
        finally:
            readonly.close()
    finally:
        store.close()


@pytest.mark.parametrize('Store', [SQLiteStore, JSONStore], ids=['sqlite', 'json'])
def test_legacy_effective_hint_cannot_be_reviewed_through_native_annotation_paths(tmp_path, Store):
    store = Store(tmp_path / 'fictional', month='2026-11')
    try:
        state = fictional_state()
        state['transactions']['out-0']['categoryId'] = 'groceries'
        store.restore_budget('personal', portable_snapshot(state))
        before, evidence = store.export_budget('personal'), store.commit_evidence()
        mutations = [
            lambda: store.edit_transaction('personal', 'out-0', reviewed=True),
            lambda: store.review_transactions('personal', ['out-0'], True),
            lambda: store.edit_transactions('personal', [{'transactionId': 'out-0', 'reviewed': True}]),
        ]
        for mutate in mutations:
            with pytest.raises(StoreError, match='exactly two'):
                mutate()
            assert store.export_budget('personal') == before
            assert store.commit_evidence() == evidence
        store.edit_transaction('personal', 'out-0', category_id='groceries', reviewed=True)
        row = next(row for row in store.transactions('personal') if row['id'] == 'out-0')
        assert row['reviewed'] and row['inferredTransfer'] and not row['isTransfer']
    finally:
        store.close()


@pytest.mark.parametrize('Store', [SQLiteStore, JSONStore], ids=['sqlite', 'json'])
def test_legacy_zero_hint_audit_and_guarded_repair_preserve_money(tmp_path, Store):
    store = Store(tmp_path / 'fictional', month='2026-11')
    try:
        state = fictional_state()
        state['categories']['transfer'] = {'id': 'transfer', 'name': 'Transfers', 'group': 'Technical',
            'target': 0, 'currency': 'USD', 'kind': 'transfer'}
        state['transactions']['out-0'].update(amount=0, categoryId='transfer', reviewed=True)
        store.restore_budget('personal', portable_snapshot(state))
        before = store.export_budget('personal')
        issue = next(row for row in store.transfer_audit('personal', {})['issues'] if row['id'] == 'out-0')
        assert issue['code'] == 'zero_amount' and issue['repairable']
        preview = store.preview_transfer_repair('personal', ['out-0'])
        assert store.export_budget('personal') == before
        with pytest.raises(StoreError, match='changed'):
            store.repair_transfers('personal', ['out-0'], 'f' * 64)
        operation = recorded(store, 'repair_transfers', {'transactionIds': ['out-0'], 'fingerprint': preview['fingerprint']})
        result = store.execute_operation(operation)
        assert store.execute_operation(operation).receipt == result.receipt
        after = store.export_budget('personal')
        repaired = next(row for row in after['transactions'] if row['id'] == 'out-0')
        assert repaired['categoryId'] is None and not repaired['reviewed']
        assert repaired['amount'] == 0 and repaired['inferredTransfer']
        assert after['accounts'] == before['accounts'] and after['assignments'] == before['assignments']
        assert repaired['note'] == state['transactions']['out-0']['note']
        assert repaired['merchant'] == state['transactions']['out-0']['merchant']
        assert repaired['original'] == state['transactions']['out-0']['original']
        with pytest.raises(StoreError):
            store.edit_transaction('personal', 'out-0', reviewed=True)
    finally:
        store.close()


def test_native_format_fence_survives_history_only_pairs_and_restoration(transfer_store, tmp_path):
    import json

    store, path, Store = transfer_store
    def version(backend):
        return (backend._authority_metadata()['formatVersion'] if Store is SQLiteStore
                else json.loads(backend.path.read_bytes())['formatVersion'])
    pairs = [{'outId': 'out-0', 'inId': 'in-0'}]
    store.apply_transfer_matches('personal', pairs)
    assert version(store) == (7 if Store is SQLiteStore else 5)
    assert portable_version(store.export_budget_files()) == (4 if Store is SQLiteStore else 2)
    entry = next(row for row in store.budget_history('personal')['entries'] if row['operation'] == 'match_transfers')
    plan = store.history_preview('personal', 'undo', entry['id'])
    store.history_apply('personal', 'undo', entry['id'], plan['fingerprint'])
    assert not any(row.get('transferPeerId') for row in store.transactions('personal'))
    assert version(store) == (7 if Store is SQLiteStore else 5)
    assert portable_version(store.export_budget_files()) == (4 if Store is SQLiteStore else 2)
    restored = Store(tmp_path / ('history-restored' + path.suffix), month='2026-11')
    local = Store(tmp_path / ('history-local' + path.suffix), month='2026-11', initial_role='staged')
    try:
        restored.restore_budget_files('personal', store.export_budget_files())
        assert version(restored) == (7 if Store is SQLiteStore else 5)
        assert portable_version(restored.export_budget_files()) == (4 if Store is SQLiteStore else 2)
        local.import_local('fictional-transfer-history', store.export_local())
        assert version(local) == (7 if Store is SQLiteStore else 5)
    finally:
        restored.close()
        local.close()
    store.close()
    readonly = Store(path, month='2026-11', read_only=True)
    try:
        assert version(readonly) == (7 if Store is SQLiteStore else 5)
    finally:
        readonly.close()


def test_cross_scope_funding_projection_keeps_complete_historical_graph(transfer_store):
    from whab.domain.budget import calculate_workspace
    from whab.domain.dates import current_month

    store, _, _ = transfer_store
    before = store.accounts('personal')
    store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}])
    for month in ('2026-10', '2026-11'):
        projected = store.workspace('personal', month)
        state = store.state('personal') if isinstance(store, SQLiteStore) else store._read('personal')
        reference = calculate_workspace(state, month, as_of_month=current_month())
        assert projected == reference
        assert projected['summary']['income'] == projected['summary']['spent'] == 0
        assert projected['summary']['transferFunding'] == (-3 if month == '2026-10' else 0)
    assert store.accounts('personal') == before


@pytest.mark.parametrize('backend', ['sqlite', 'json'])
def test_native_process_loss_after_bulk_commit_replays_without_second_save(tmp_path, backend):
    import json
    import subprocess
    import sys
    from pathlib import Path

    path, command_path = tmp_path / ('fictional.' + backend), tmp_path / 'request.json'
    child = '''
import json, os, runpy, sys
from pathlib import Path
helpers=runpy.run_path(sys.argv[1])
Store=helpers['SQLiteStore'] if sys.argv[2]=='sqlite' else helpers['JSONStore']
store=Store(sys.argv[3],month='2026-11')
store.restore_budget('personal',helpers['portable_snapshot'](helpers['fictional_state']()))
pairs=[{'outId':'out-0','inId':'in-0'},{'outId':'out-1','inId':'in-1'}]
preview=store.preview_transfer_matches('personal',pairs,reviewed=True)
command=helpers['recorded'](store,'apply_transfer_matches',{'pairs':pairs,'reviewed':True,'fingerprint':preview['fingerprint']})
Path(sys.argv[4]).write_text(json.dumps({'operation_id':command.operation_id,'authority_epoch':command.authority_epoch,
 'expected_generation':command.expected_generation,'workspace':command.workspace,'name':command.name,'payload':command.payload.decode()}))
store.execute_operation(command)
os._exit(17)
'''
    child_result = subprocess.run([sys.executable, '-c', child, str(Path(__file__).resolve()), backend,
        str(path), str(command_path)], capture_output=True, text=True, timeout=30)
    assert child_result.returncode == 17, child_result.stderr
    data = json.loads(command_path.read_text())
    operation = StorageOperation(**{**data, 'payload': data['payload'].encode()})
    Store = SQLiteStore if backend == 'sqlite' else JSONStore
    store = Store(path, month='2026-11')
    try:
        evidence, saved = store.commit_evidence(), store.export_budget('personal')
        assert evidence.generation == operation.expected_generation + 1
        assert store.execute_operation(operation).receipt.evidence == evidence
        assert store.commit_evidence() == evidence and store.export_budget('personal') == saved
        assert len([entry for entry in store.budget_history('personal')['entries']
                    if entry['operation'] == 'match_transfers']) == 1
    finally:
        store.close()


def test_sqlite_unmanaged_fictional_image_uses_same_atomic_pair_rules(tmp_path):
    # Sanitized image construction deliberately has no runtime authority tables.
    store = SQLiteStore(tmp_path / 'fictional-image.sqlite', month='2026-11', _authority=False)
    try:
        store.restore_budget('personal', portable_snapshot(fictional_state()))
        before = store.accounts('personal')
        result = store.apply_transfer_matches('personal', [{'outId': 'out-0', 'inId': 'in-0'}])
        assert result['count'] == 2
        assert store.state('personal')['transactions']['out-0']['transferPeerId'] == 'in-0'
        assert store.accounts('personal') == before
    finally:
        store.close()
