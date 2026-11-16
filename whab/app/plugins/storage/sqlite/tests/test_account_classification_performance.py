"""Metadata classification reads only a bounded confirmed-transfer witness."""

import sqlite3

import pytest
from whab_plugin_sqlite import Store, portable
from whab_plugin_sqlite.portable_journal import read as read_journal

from whab.plugins import StoreError
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def command(store, account, changes, identifier):
    evidence = store.commit_evidence()
    return store.execute_operation(StorageOperation(identifier, evidence.authority_epoch, evidence.generation,
        'personal', 'update_account', canonical_storage_json({'accountId': account, **changes})))


@pytest.fixture
def account_ledger(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-11')
    account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
    other = store.add_account('personal', {'name': 'Fictional savings', 'type': 'depository', 'balance': 10000})
    ids = [store.add_transaction('personal', {'accountId': target, 'date': '2026-11-01',
           'merchant': 'Fictional transfer', 'amount': amount, 'categoryId': None})
           for target, amount in ((account, 100), (other, -100))]
    yield store, account, other, ids
    store.close()


def test_on_budget_classification_uses_metadata_path_and_preserves_native_portable_history(account_ledger, monkeypatch):
    store, account, _, _ = account_ledger
    command(store, account, {'type': 'credit', 'onBudget': True}, 'warm')
    store.export_budget_files()
    before = store.export_budget('personal')
    queries = []
    read = store._read_state
    def bounded(workspace, **kwargs):
        assert kwargs.get('transaction_ids') == ()
        return read(workspace, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(store, '_mutate', lambda *_: pytest.fail('Metadata classification must not rewrite the ledger'))
        patch.setattr(store, '_read_state', bounded)
        store.db.set_trace_callback(queries.append)
        try:
            saved = command(store, account, {'type': 'depository', 'onBudget': False}, 'classify')
        finally:
            store.db.set_trace_callback(None)
    after = store.export_budget('personal')
    assert after['transactions'] == before['transactions']
    assert len(after['history']['entries']) == len(before['history']['entries']) + 1
    assert next(row for row in after['accounts'] if row['id'] == account)['onBudget'] is False
    assert any('transfer_peer_id IS NOT NULL LIMIT 1' in sql for sql in queries)
    assert portable.read_files(store.export_budget_files()) == after
    assert store.operation_receipt('classify') == saved.receipt


@pytest.mark.parametrize('changes', [{'onBudget': False}, {'type': 'investment'}])
def test_explicit_and_implicit_scope_changes_refuse_confirmed_pair_without_partial_write(account_ledger, changes):
    store, account, _, ids = account_ledger
    preview = store.preview_transfer_match('personal', ids, 'match')
    store.apply_transfer_match('personal', ids, 'match', preview['fingerprint'])
    before = store.export_budget('personal')
    evidence = store.commit_evidence()
    with pytest.raises(StoreError, match='Unmatch confirmed transfers'):
        command(store, account, changes, 'refused')
    assert store.commit_evidence() == evidence
    assert store.export_budget('personal') == before
    assert store.operation_receipt('refused') is None
    command(store, account, {'type': 'credit', 'onBudget': True}, 'same-scope')
    assert store.export_budget('personal')['transactions'] == before['transactions']


def test_metadata_errors_currency_and_closed_keep_original_core_validation(account_ledger, monkeypatch):
    store, account, _, _ = account_ledger
    with pytest.raises(StoreError, match='Account not found'):
        store.update_account('personal', 'absent', {'onBudget': False})
    with pytest.raises(StoreError, match='Invalid account classification fields'):
        store.update_account('personal', account, {})
    store.update_account('personal', account, {'name': 'Renamed fictional account'})
    complete = store._mutate
    calls = []
    def full(*args, **kwargs):
        calls.append(args[0])
        return complete(*args, **kwargs)
    monkeypatch.setattr(store, '_mutate', full)
    with pytest.raises(StoreError, match='Currency can only change'):
        store.update_account('personal', account, {'currency': 'CAD'})
    store.update_account('personal', account, {'closed': True})
    assert calls == ['personal', 'personal']
    assert next(row for row in store.accounts('personal') if row['id'] == account)['closed'] is True


@pytest.mark.parametrize('count', [1000, 100000])
def test_confirmed_transfer_lookup_vm_work_is_bounded_with_large_account_history(count):
    # Isolate SQL work from one-time native integrity reconstruction. Actual guarded
    # saves and portable restoration are exercised separately above and by the CLI matrix.
    store = Store(':memory:', month='2026-11', _authority=False)
    try:
        account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
        store.db.executemany("INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source) VALUES(?,'personal',?,'2026-11-01','Fictional',100,'manual')",
                            ((f'fictional-{i}', account) for i in range(count)))
        plan = store.db.execute('EXPLAIN QUERY PLAN SELECT id,account_id,transfer_peer_id FROM transactions '
             'WHERE workspace=? AND account_id=? AND transfer_peer_id IS NOT NULL LIMIT 1', ('personal', account)).fetchall()
        assert any('tx_confirmed_transfer_account' in row[3] for row in plan)
        steps = []
        store.db.set_progress_handler(lambda: steps.append(1) or 0, 100)
        try:
            store.update_account('personal', account, {'type': 'credit', 'onBudget': True})
        finally:
            store.db.set_progress_handler(None, 0)
        assert len(steps) < 10, 'Classification walked transaction history instead of the partial index'
    finally:
        store.close()


def test_transfer_index_is_optional_exact_and_native_only(account_ledger, tmp_path):
    store, account, _, _ = account_ledger
    raw = read_journal(store.export_budget_files()['budget.sqlite'])[0]
    image = sqlite3.connect(':memory:')
    try:
        image.deserialize(raw)
        assert not image.execute("SELECT 1 FROM sqlite_schema WHERE name='tx_confirmed_transfer_account'").fetchone()
        definition = store.db.execute("SELECT sql FROM sqlite_schema WHERE name='tx_confirmed_transfer_account'").fetchone()[0]
        image.execute(definition)
        assert portable.read_files({'budget.sqlite': image.serialize()}) == store.export_budget('personal')
        image.execute('DROP INDEX tx_confirmed_transfer_account')
        image.execute('CREATE INDEX tx_confirmed_transfer_account ON transactions(workspace,amount)')
        with pytest.raises(StoreError, match='unsupported database schema'):
            portable.read_files({'budget.sqlite': image.serialize()})
    finally:
        image.close()
    store.db.execute('DROP INDEX tx_confirmed_transfer_account')
    reader = Store(store.database_path, read_only=True)
    try:
        assert reader.accounts('personal') == store.accounts('personal')
    finally:
        reader.close()
    # Absence remains correct for previously opened stores; writable reopen adds it.
    store.update_account('personal', account, {'onBudget': False})
    reopened = Store(store.database_path)
    try:
        assert reopened.db.execute("SELECT 1 FROM sqlite_schema WHERE name='tx_confirmed_transfer_account'").fetchone()
    finally:
        reopened.close()


@pytest.mark.parametrize('with_index', [False, True])
def test_bulk_classification_preserves_scope_guard_with_optional_index(account_ledger, with_index):
    store, account, other, ids = account_ledger
    preview = store.preview_transfer_match('personal', ids, 'match')
    store.apply_transfer_match('personal', ids, 'match', preview['fingerprint'])
    if not with_index:
        store.db.execute('DROP INDEX tx_confirmed_transfer_account')
    before = store.export_budget('personal')
    changes = [{'accountId': account, 'onBudget': False}, {'accountId': other, 'onBudget': False}]
    for operation in (lambda: store.preview_bulk_edits('personal', 'classify_accounts', changes),
                      lambda: store.classify_accounts('personal', changes)):
        with pytest.raises(StoreError, match='Unmatch confirmed transfers'):
            operation()
        assert store.export_budget('personal') == before


def test_bulk_scope_guard_reads_two_witnesses_from_100k_paired_transactions(monkeypatch):
    store = Store(':memory:', month='2026-11', _authority=False)
    try:
        accounts = [store.add_account('personal', {'name': f'Fictional {i}', 'type': 'depository', 'balance': 10000}) for i in range(2)]
        store.db.executemany("INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,source,transfer_peer_id,is_transfer) VALUES(?,'personal',?,'2026-11-01','Fictional',?,'manual',?,1)",
                            ((f'paired-{i}', accounts[i % 2], 100 if i % 2 == 0 else -100, f'paired-{i ^ 1}') for i in range(100000)))
        selected = []
        original = store._read_state
        def counted(workspace, **kwargs):
            selected.append(tuple(kwargs['transaction_ids']))
            return original(workspace, **kwargs)
        monkeypatch.setattr(store, '_read_state', counted)
        steps = []
        store.db.set_progress_handler(lambda: steps.append(1) or 0, 100)
        try:
            with pytest.raises(StoreError, match='Unmatch confirmed transfers'):
                store.preview_bulk_edits('personal', 'classify_accounts', [
                    {'accountId': account, 'onBudget': False} for account in accounts
                ])
        finally:
            store.db.set_progress_handler(None, 0)
        assert [len(ids) for ids in selected] == [0, 2]
        assert len(steps) < 20, 'Bulk scope guard traversed all confirmed transfers'
    finally:
        store.close()


def test_bulk_unmatched_accounts_change_scope_without_loading_transactions(account_ledger, monkeypatch):
    store, account, other, _ = account_ledger
    changes = [{'accountId': key, 'onBudget': False} for key in (account, other)]
    original = store._read_state
    selected = []
    def bounded(workspace, **kwargs):
        selected.append(kwargs.get('transaction_ids'))
        return original(workspace, **kwargs)
    monkeypatch.setattr(store, '_read_state', bounded)
    preview = store.preview_bulk_edits('personal', 'classify_accounts', changes)
    assert selected == [()]
    assert preview['count'] == preview['changed'] == 2
    saved = store.classify_accounts('personal', changes)
    assert saved == preview
    assert all(not row['onBudget'] for row in store.accounts('personal'))
