"""Fictional Opening Balance data keeps native receipts, history and balances exact."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_sqlite import Store

from whab.plugins import StoreError, WorkspaceImport
from whab.plugins.storage_transfer import StorageOperation, canonical_storage_json


def seed(store):
    accounts = tuple({'id': identity, 'name': name, 'institution': 'Fictional Bank',
                      'type': kind, 'balance': balance, 'source': 'fictional',
                      'stale': False, 'balanceUnknown': False, 'onBudget': on_budget}
                     for identity, name, kind, balance, on_budget in [
                         ('cash', 'Fictional 旅行 Cash', 'depository', 50000, True),
                         ('credit', 'Fictional Credit', 'credit', 10000, True),
                         ('tracking', 'Fictional Tracking', 'depository', 70000, False)])
    rows = tuple({'id': identity, 'accountId': account, 'date': '2026-10-01',
                  'merchant': '  Fictional initial balance  ', 'amount': amount, 'categoryId': category,
                  'pending': pending, 'reviewed': False, 'source': 'fictional',
                  'note': ' [YNAB: cleared]\n original  ', 'isTransfer': False,
                  'inferredTransfer': False, 'categoryEdited': False, 'deleted': False}
                 for identity, account, amount, category, pending in [
                     ('cash-start', 'cash', -50000, 'starting', False),
                     ('credit-start', 'credit', -10000, 'starting', False),
                     ('tracking-start', 'tracking', -70000, 'starting', False),
                     ('zero-start', 'cash', 0, None, False),
                     ('pending-zero', 'cash', 0, None, True)])
    store.import_workspace('personal', 'fictional', 'f' * 64, WorkspaceImport(
        accounts, ({'id': 'starting', 'name': 'Beginning 💰', 'group': 'Finance', 'target': 0, 'kind': 'income'},),
        rows, (), '2026-10'))


def operation(store, name, parameters):
    evidence = store.commit_evidence()
    identifier = uuid4().hex
    return StorageOperation(identifier, evidence.authority_epoch, evidence.generation, 'personal',
        'recorded_command', canonical_storage_json({'operation': name, 'parameters': parameters,
            'context': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-opening',
                        'requestId': identifier, 'operation': name}}))


def test_opening_classification_preview_atomic_retry_restart_history_and_actual_saved_restore(tmp_path):
    path = tmp_path / 'fictional.sqlite'
    store = Store(path, month='2026-10')
    try:
        seed(store)
        assert store.supports_opening_balances()
        before = deepcopy(store.export_budget('personal'))
        demo = store.export_budget('demo')
        observed = store.commit_evidence()
        preview = store.preview_category_classification('personal', 'starting', 'opening_balance')
        assert preview['after']['kind'] == 'opening_balance'
        assert preview['impacts'][0]['after']['openingFunding'] == 50000
        assert store.commit_evidence() == observed and store.export_budget('personal') == before
        command = operation(store, 'classify_category', {'categoryId': 'starting', 'kind': 'opening_balance'})
        saved = store.execute_operation(command)
        assert saved.receipt.evidence.generation == observed.generation + 1
        after = deepcopy(store.export_budget('personal'))
        assert after['accounts'] == before['accounts'] and after['transactions'] == before['transactions']
        assert after['assignments'] == before['assignments']
        assert store.export_budget('demo') == demo
        summary = store.workspace('personal', '2026-10')['summary']
        assert summary['openingFunding'] == 50000 and summary['income'] == 0 and summary['spent'] == 0
        found = {row['id']: row for row in store.search_transactions('personal', {})['data']}
        assert found['cash-start']['categoryKind'] == 'opening_balance'
        assert found['cash-start']['categoryName'] == 'Beginning 💰'
        assert found['zero-start']['categoryKind'] is None
        entry = store.budget_history('personal')['entries'][0]
        assert entry['operation'] == 'classify_category' and entry['reversible'] and entry['changeCount'] == 1
        assert entry['actor'] == 'Codex' and entry['batchId'] == 'fictional-opening'
        files = store.export_budget_files('personal')
        with pytest.raises(StoreError):
            store.execute_operation(replace(command, operation_id=uuid4().hex))
        with pytest.raises(StoreError):
            store.execute_operation(replace(command, payload=canonical_storage_json({
                'operation': 'classify_category', 'parameters': {'categoryId': 'starting', 'kind': 'expense'},
                'context': {'actor': 'Codex', 'actorKind': 'agent'}})))
        assert store.export_budget('personal') == after and store.commit_evidence() == saved.receipt.evidence
    finally:
        store.close()
    restored = Store(tmp_path / 'empty-restored.sqlite', month='2026-10')
    try:
        restored.restore_budget_files('personal', files)
        copy = restored.export_budget('personal')
        assert copy['history'] == after['history']
        assert copy['accounts'] == [{**row, 'stale': True} for row in after['accounts']]
        assert copy['transactions'] == after['transactions']
        assert copy['categories'] == after['categories']
        # Portable restore marks provider snapshots stale; it never verifies imported cash.
        assert restored.workspace('personal', '2026-10')['summary']['openingFunding'] == 0
    finally:
        restored.close()
    reopened = Store(path, month='2026-10')
    try:
        assert reopened.execute_operation(command).receipt == saved.receipt
        assert reopened.commit_evidence() == saved.receipt.evidence and reopened.export_budget('personal') == after
        for action, expected in [('undo', before), ('redo', after)]:
            plan = reopened.history_preview('personal', action, entry['id'])
            reopened.history_apply('personal', action, entry['id'], plan['fingerprint'])
            actual = reopened.export_budget('personal')
            assert actual['categories'] == expected['categories']
            assert {row['id']: row for row in actual['transactions']} == {row['id']: row for row in before['transactions']}
            assert actual['accounts'] == before['accounts']
    finally:
        reopened.close()


def test_existing_zero_annotation_review_bulk_and_invalid_plan_preserve_financial_state(tmp_path):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-10')
    try:
        seed(store)
        store.classify_category('personal', 'starting', 'opening_balance')
        before = deepcopy(store.export_budget('personal'))
        command = operation(store, 'edit_transactions', {'edits': [
            {'transactionId': 'zero-start', 'categoryId': 'starting', 'categoryAgent': 'Codex', 'reviewed': True},
            {'transactionId': 'cash-start', 'reviewed': True}]})
        saved = store.execute_operation(command)
        after = deepcopy(store.export_budget('personal'))
        rows = {row['id']: row for row in after['transactions']}
        assert rows['zero-start']['amount'] == 0 and rows['zero-start']['reviewed']
        assert rows['zero-start']['note'] == ' [YNAB: cleared]\n original  '
        assert rows['zero-start']['merchant'] == '  Fictional initial balance  '
        assert rows['cash-start']['reviewed']
        assert after['accounts'] == before['accounts']
        assert store.execute_operation(command).receipt == saved.receipt
        assert store.export_budget('personal') == after and store.commit_evidence() == saved.receipt.evidence
        with pytest.raises(StoreError):
            store.edit_transactions('personal', [
                {'transactionId': 'zero-start', 'note': 'must not save'},
                {'transactionId': 'pending-zero', 'categoryId': 'starting', 'reviewed': True}])
        with pytest.raises(StoreError):
            store.edit_transaction_details('personal', 'zero-start', {'amount': 0})
        with pytest.raises(StoreError, match='cannot receive monthly assignments'):
            store.assign('personal', 'starting', '2026-10', 0)
        assert store.export_budget('personal') == after and store.commit_evidence() == saved.receipt.evidence
        store.edit_transaction('personal', 'zero-start', category_id=None)
        assert not next(row for row in store.transactions('personal') if row['id'] == 'zero-start')['reviewed']
    finally:
        store.close()


def test_native_failure_rolls_back_category_history_receipt_and_format_floor(tmp_path):
    import sqlite3

    store = Store(tmp_path / 'fictional.sqlite', month='2026-10')
    try:
        seed(store)
        command = operation(store, 'classify_category', {'categoryId': 'starting', 'kind': 'opening_balance'})
        before = store.export_budget('personal')
        evidence = store.commit_evidence()
        store.db.execute("CREATE TRIGGER fictional_classification_failure BEFORE INSERT ON categories "
                         "WHEN NEW.kind='opening_balance' BEGIN SELECT RAISE(ABORT,'fictional full disk'); END")
        with pytest.raises(sqlite3.IntegrityError, match='fictional full disk'):
            store.execute_operation(command)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        assert store.operation_receipt(command.operation_id) is None
        assert store._authority_metadata()['formatVersion'] == 5
        store.db.execute('DROP TRIGGER fictional_classification_failure')
        assert store.execute_operation(command).receipt.evidence.generation == evidence.generation + 1
        assert store._authority_metadata()['formatVersion'] == 5
    finally:
        store.close()


def test_process_loss_after_commit_recovers_exact_request_without_second_generation(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    path = tmp_path / 'fictional.sqlite'
    request = tmp_path / 'fictional-request.json'
    script = '''
import json, os, runpy, sys
from pathlib import Path
from whab_plugin_sqlite import Store
helpers = runpy.run_path(sys.argv[1])
store = Store(sys.argv[2], month='2026-10')
helpers['seed'](store)
command = helpers['operation'](store, 'classify_category', {'categoryId': 'starting', 'kind': 'opening_balance'})
Path(sys.argv[3]).write_text(json.dumps({'operation_id': command.operation_id, 'authority_epoch': command.authority_epoch,
 'expected_generation': command.expected_generation, 'workspace': command.workspace, 'name': command.name,
 'payload': command.payload.decode()}))
store.execute_operation(command)
os._exit(17)
'''
    result = subprocess.run([sys.executable, '-c', script, str(Path(__file__).resolve()), str(path), str(request)],
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 17, result.stderr
    value = json.loads(request.read_text())
    command = StorageOperation(**{**value, 'payload': value['payload'].encode()})
    store = Store(path, month='2026-10')
    try:
        before = store.export_budget('personal')
        evidence = store.commit_evidence()
        assert evidence.generation == command.expected_generation + 1
        receipt = store.execute_operation(command).receipt
        assert receipt.evidence == evidence
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        assert store._authority_metadata()['formatVersion'] == 5
        assert len([entry for entry in store.budget_history('personal')['entries'] if entry['operation'] == 'classify_category']) == 1
    finally:
        store.close()
