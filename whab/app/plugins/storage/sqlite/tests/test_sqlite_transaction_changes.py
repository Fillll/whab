"""Native edit/create plans commit and recover as one coherent fictional change."""

import sqlite3
from copy import deepcopy
from uuid import uuid4

import pytest
from whab_plugin_sqlite import Store

from whab.plugins import StoreError, WorkspaceImport
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    read_storage_json,
)

CREATION_ID = '00000000-0000-4000-8000-000000000001'


def seed(store):
    account = {'id': 'fictional-savings', 'name': 'Fictional 旅行 Savings', 'institution': 'Fictional Bank',
               'type': 'depository', 'balance': 50000, 'source': 'fictional',
               'stale': False, 'balanceUnknown': False}
    categories = ({'id': 'residence', 'name': 'Residence', 'group': 'House', 'target': 0},
                  {'id': 'transfers', 'name': 'Transfers', 'group': 'Finance', 'target': 0, 'kind': 'transfer'})
    transaction = {'id': 'fictional-import', 'accountId': account['id'], 'date': '2026-10-02',
                   'merchant': '  Fictional property receipt  ', 'amount': -100000, 'categoryId': 'residence',
                   'pending': False, 'reviewed': True, 'source': 'fictional', 'note': ' [YNAB: cleared]\n original  ',
                   'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True, 'deleted': False}
    payment = {**transaction, 'id': 'fictional-payment', 'amount': 100000, 'merchant': 'Separate fictional payment'}
    store.import_workspace('personal', 'fictional', 'f' * 64, WorkspaceImport(
        accounts=(account,), categories=categories, transactions=(transaction, payment), assignments=(), budget_start_month='2026-10'))
    edits = [{'transactionId': transaction['id'], 'amount': -80000}]
    creates = [{'creationId': CREATION_ID, 'accountId': account['id'], 'date': transaction['date'],
                'merchant': 'Fictional lender', 'amount': -20000, 'currency': 'USD', 'categoryId': 'transfers',
                'note': transaction['note'], 'categoryAgent': 'Codex'}]
    return edits, creates


def native_operation(store, edits, creates, *, preserve=True):
    evidence = store.commit_evidence()
    return StorageOperation(uuid4().hex, evidence.authority_epoch, evidence.generation, 'personal',
                            'change_transactions', canonical_storage_json({
                                'edits': edits, 'creates': creates, 'preserveBalances': preserve}))


def test_split_preview_commit_restart_exact_retry_and_guarded_undo_redo(tmp_path):
    path = tmp_path / 'fictional.sqlite'
    store = Store(path, month='2026-11')
    try:
        edits, creates = seed(store)
        before = deepcopy(store.export_budget('personal'))
        evidence = store.commit_evidence()
        assert store.supports_atomic_transaction_changes()
        preview = store.preview_transaction_changes('personal', edits, creates, preserve_balances=True)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        operation = native_operation(store, edits, creates)
        result = store.execute_operation(operation)
        assert read_storage_json(result.receipt.result) == preview
        assert result.receipt.evidence.generation == evidence.generation + 1
        saved = deepcopy(store.export_budget('personal'))
        transactions = {row['id']: row for row in saved['transactions']}
        old = {row['id']: row for row in before['transactions']}
        assert transactions['fictional-import']['merchant'] == old['fictional-import']['merchant']
        assert transactions['fictional-import']['note'] == old['fictional-import']['note']
        assert transactions['fictional-import']['original']['amount'] == -100000
        assert not transactions['fictional-import']['reviewed']
        assert transactions[CREATION_ID]['source'] == 'manual' and not transactions[CREATION_ID]['reviewed']
        assert transactions['fictional-payment'] == old['fictional-payment']
        assert saved['accounts'] == before['accounts']
        assert sum(row['amount'] for row in transactions.values()) == sum(row['amount'] for row in old.values())
        history = store.budget_history('personal')['entries'][0]
        assert history['operation'] == 'change_transactions' and history['reversible']
        assert history['changeCount'] == 2
        expected_receipt = result.receipt
        files = store.export_budget_files('personal')
    finally:
        store.close()

    restored = Store(tmp_path / 'empty-restored.sqlite', month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        copy = restored.export_budget('personal')
        assert {row['id']: row for row in copy['transactions']} == {row['id']: row for row in saved['transactions']}
        assert copy['history'] == saved['history']
    finally:
        restored.close()
    store = Store(path, month='2026-11')
    try:
        generation = store.commit_evidence().generation
        assert store.execute_operation(operation).receipt == expected_receipt
        assert store.commit_evidence().generation == generation and store.export_budget('personal') == saved
        for action, expected in [('undo', before), ('redo', saved)]:
            plan = store.history_preview('personal', action, history['id'])
            store.history_apply('personal', action, history['id'], plan['fingerprint'])
            actual = store.export_budget('personal')
            assert actual['transactions'] == expected['transactions']
            assert actual['accounts'] == expected['accounts']
    finally:
        store.close()


def test_native_create_failure_rolls_back_edited_original_history_and_receipt(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        edits, creates = seed(store)
        store.db.execute("CREATE TRIGGER fictional_split_failure BEFORE INSERT ON transactions "
                         "WHEN NEW.id='00000000-0000-4000-8000-000000000001' "
                         "BEGIN SELECT RAISE(ABORT,'fictional split fault'); END")
        before = store.export_budget('personal')
        evidence = store.commit_evidence()
        operation = native_operation(store, edits, creates)
        with pytest.raises(sqlite3.IntegrityError, match='fictional split fault'):
            store.execute_operation(operation)
        assert store.export_budget('personal') == before
        assert store.commit_evidence() == evidence and store.operation_receipt(operation.operation_id) is None
    finally:
        store.close()


def test_direct_create_edit_and_identity_collision_are_atomic(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        _, creates = seed(store)
        before_demo = store.export_budget('demo')
        before_balance = store.accounts('personal')[0]['balance']
        first = store.change_transactions('personal', [], creates)
        assert first['createdIds'] == [CREATION_ID]
        assert store.accounts('personal')[0]['balance'] == before_balance + 20000
        edit = [{'transactionId': CREATION_ID, 'amount': -100, 'merchant': 'Corrected lender'}]
        preview = store.preview_transaction_changes('personal', edit, [])
        assert store.change_transactions('personal', edit, []) == preview
        assert store.accounts('personal')[0]['balance'] == before_balance + 100
        before = store.export_budget('personal')
        evidence = store.commit_evidence()
        with pytest.raises(StoreError):
            store.change_transactions('personal', [{'transactionId': 'fictional-import', 'note': 'must not save'}], creates)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        assert store.export_budget('demo') == before_demo
    finally:
        store.close()
