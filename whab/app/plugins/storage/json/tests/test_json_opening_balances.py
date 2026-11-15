"""Fictional Opening Balance data keeps native receipts, history and balances exact."""

from copy import deepcopy
from dataclasses import replace
from uuid import uuid4

import pytest
from whab_plugin_json import Store

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
    path = tmp_path / 'fictional.json'
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
    restored = Store(tmp_path / 'empty-restored.json', month='2026-10')
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
    store = Store(tmp_path / 'fictional.json', month='2026-10')
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


@pytest.mark.parametrize('boundary', ['before_replace', 'replaced', 'published'])
def test_publication_failure_or_uncertainty_keeps_one_classification_receipt(tmp_path, boundary):
    from whab_plugin_json import CommitUncertain

    path = tmp_path / 'fictional.json'
    store = Store(path, month='2026-10')
    try:
        seed(store)
        command = operation(store, 'classify_category', {'categoryId': 'starting', 'kind': 'opening_balance'})
        before = store.export_budget('personal')
        evidence = store.commit_evidence()

        def fail(stage):
            if stage == boundary:
                raise OSError('Fictional lost response')

        store._hook = fail
        with pytest.raises(StoreError if boundary == 'before_replace' else CommitUncertain):
            store.execute_operation(command)
        store._hook = None
        if boundary == 'before_replace':
            assert store.export_budget('personal') == before and store.commit_evidence() == evidence
            assert store.operation_receipt(command.operation_id) is None
    finally:
        store.close()
    reopened = Store(path, month='2026-10')
    try:
        saved = reopened.execute_operation(command)
        assert saved.receipt.evidence.generation == evidence.generation + 1
        assert next(row for row in reopened.categories('personal') if row['id'] == 'starting')['kind'] == 'opening_balance'
        assert len([entry for entry in reopened.budget_history('personal')['entries'] if entry['operation'] == 'classify_category']) == 1
        assert reopened.execute_operation(command).receipt == saved.receipt
    finally:
        reopened.close()


def test_json_format_fence_retains_history_only_kind_across_saved_and_local_restore(tmp_path):
    import json

    source = Store(tmp_path / 'fictional.json', month='2026-10')
    restored = Store(tmp_path / 'restored.json', month='2026-10')
    local = Store(tmp_path / 'local.json', month='2026-10', initial_role='staged')
    try:
        assert json.loads(source.path.read_bytes())['formatVersion'] == 3
        category = source.add_category('personal', {'name': 'Beginning', 'group': 'Finance', 'kind': 'opening_balance'})
        assert json.loads(source.path.read_bytes())['formatVersion'] == 4
        source.add_category('personal', {'name': 'Salary', 'group': 'Finance', 'kind': 'income'})
        with source.history_scope({'operation': 'delete_category'}):
            source.delete_category('personal', category)
        assert all(row.get('kind') != 'opening_balance' for row in source.categories('personal'))
        assert json.loads(source.path.read_bytes())['formatVersion'] == 4
        files = source.export_budget_files('personal')
        restored.restore_budget_files('personal', files)
        assert json.loads(restored.path.read_bytes())['formatVersion'] == 4
        local.import_local('fictional-opening-history', source.export_local())
        assert json.loads(local.path.read_bytes())['formatVersion'] == 4
        entry = next(row for row in source.budget_history('personal')['entries'] if row['operation'] == 'delete_category')
        for action in ('undo', 'redo'):
            plan = source.history_preview('personal', action, entry['id'])
            source.history_apply('personal', action, entry['id'], plan['fingerprint'])
            assert json.loads(source.path.read_bytes())['formatVersion'] == 4
    finally:
        source.close()
        restored.close()
        local.close()
