"""JSON edit/create plans publish once and never leave half a fictional split."""

from copy import deepcopy
from uuid import uuid4

import pytest
from whab_plugin_json import CommitUncertain, Store

from whab.plugins import StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    read_storage_json,
)


def seed(store):
    account = store.add_account('personal', {'name': 'Fictional Savings', 'type': 'depository', 'balance': 100000})
    category = store.add_category('personal', {'name': 'Residence', 'group': 'House', 'target': 0})
    original = store.add_transaction('personal', {'accountId': account, 'date': '2026-10-02',
        'merchant': 'Fictional property receipt', 'amount': -100000, 'categoryId': category,
        'note': ' [YNAB: cleared]\n original  '}, source='fictional')
    store.edit_transaction('personal', original, reviewed=True)
    edits = [{'transactionId': original, 'amount': -80000}]
    creates = [{'creationId': str(uuid4()), 'accountId': account, 'date': '2026-10-02',
                'merchant': 'Fictional lender', 'amount': -20000, 'currency': 'USD', 'categoryId': category,
                'note': 'Separate fictional share'}]
    return account, original, edits, creates


def operation(store, edits, creates):
    evidence = store.commit_evidence()
    return StorageOperation(uuid4().hex, evidence.authority_epoch, evidence.generation, 'personal',
                            'change_transactions', canonical_storage_json({
                                'edits': edits, 'creates': creates, 'preserveBalances': True}))


def test_json_plan_preview_and_single_publication_survive_restart_and_history(tmp_path):
    path = tmp_path / 'fictional.json'
    store = Store(path, month='2026-11')
    try:
        account, original, edits, creates = seed(store)
        before = deepcopy(store.export_budget('personal'))
        evidence = store.commit_evidence()
        assert store.supports_atomic_transaction_changes()
        preview = store.preview_transaction_changes('personal', edits, creates, preserve_balances=True)
        assert store.export_budget('personal') == before and store.commit_evidence() == evidence
        command = operation(store, edits, creates)
        saved = store.execute_operation(command)
        assert read_storage_json(saved.receipt.result) == preview
        assert store.commit_evidence().generation == evidence.generation + 1
        after = deepcopy(store.export_budget('personal'))
        assert before['accounts'] == after['accounts']
        rows = {row['id']: row for row in after['transactions']}
        assert rows[original]['note'] == ' [YNAB: cleared]\n original  '
        assert rows[original]['original']['amount'] == -100000
        assert not rows[original]['reviewed']
        assert rows[creates[0]['creationId']]['source'] == 'manual'
        assert sum(row['amount'] for row in after['transactions']) == sum(row['amount'] for row in before['transactions'])
        entry = store.budget_history('personal')['entries'][0]
        assert entry['reversible'] and entry['changeCount'] == 2
        files = store.export_budget_files('personal')
    finally:
        store.close()

    restored = Store(tmp_path / 'empty-restored.json', month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        copy = restored.export_budget('personal')
        assert {row['id']: row for row in copy['transactions']} == {row['id']: row for row in after['transactions']}
        assert copy['history'] == after['history']
    finally:
        restored.close()
    store = Store(path, month='2026-11')
    try:
        evidence = store.commit_evidence()
        assert store.execute_operation(command).receipt == saved.receipt
        assert store.commit_evidence() == evidence and store.export_budget('personal') == after
        for action, expected in [('undo', before), ('redo', after)]:
            plan = store.history_preview('personal', action, entry['id'])
            store.history_apply('personal', action, entry['id'], plan['fingerprint'])
            actual = store.export_budget('personal')
            assert {row['id']: row for row in actual['transactions']} == {row['id']: row for row in expected['transactions']}
            assert actual['accounts'] == expected['accounts']
    finally:
        store.close()


def test_json_failure_before_replace_preserves_both_records_history_and_receipt(json_store):
    _, _, edits, creates = seed(json_store)
    before = json_store.path.read_bytes()
    evidence = json_store.commit_evidence()
    command = operation(json_store, edits, creates)

    def fail(stage):
        if stage == 'before_replace':
            raise OSError('Fictional no space')

    json_store._hook = fail
    with pytest.raises(StoreError, match='previous generation remains authoritative'):
        json_store.execute_operation(command)
    json_store._hook = None
    assert json_store.path.read_bytes() == before
    assert json_store.commit_evidence() == evidence and json_store.operation_receipt(command.operation_id) is None
    result = json_store.execute_operation(command)
    assert result.receipt.evidence.generation == evidence.generation + 1


def test_json_direct_changes_do_not_normalize_omitted_annotations_and_refuse_entire_invalid_plan(json_store):
    _, original, _, creates = seed(json_store)
    first = json_store.change_transactions('personal', [], creates)
    assert first['createdIds'] == [creates[0]['creationId']]
    before = deepcopy(json_store.export_budget('personal'))
    with pytest.raises(StoreError):
        json_store.change_transactions('personal', [{'transactionId': original, 'note': 'must not save'},
            {'transactionId': 'missing', 'amount': 100}], [])
    assert json_store.export_budget('personal') == before
    preview = json_store.preview_transaction_changes('personal', [{'transactionId': original, 'amount': -100}], [])
    assert json_store.change_transactions('personal', [{'transactionId': original, 'amount': -100}], []) == preview
    assert preview['changes'][0]['after']['note'] == ' [YNAB: cleared]\n original  '


@pytest.mark.parametrize('stage', ['replaced', 'published'])
def test_json_uncertain_split_reopens_with_one_receipt_and_no_duplicate_create(tmp_path, stage):
    path = tmp_path / 'fictional.json'
    store = Store(path, month='2026-11')
    try:
        _, _, edits, creates = seed(store)
        command = operation(store, edits, creates)
        previous = store.commit_evidence()

        def interrupt(boundary):
            if boundary == stage:
                raise OSError('Fictional lost durable-save response')

        store._hook = interrupt
        with pytest.raises(CommitUncertain):
            store.execute_operation(command)
        with pytest.raises(CommitUncertain):
            store.execute_operation(command)
    finally:
        store.close()
    reopened = Store(path, month='2026-11')
    try:
        before = deepcopy(reopened.export_budget('personal'))
        saved = reopened.execute_operation(command)
        assert saved.receipt.evidence.generation == previous.generation + 1
        assert reopened.commit_evidence() == saved.receipt.evidence
        assert reopened.export_budget('personal') == before
        assert len(before['transactions']) == 2
        assert [row['id'] for row in before['transactions']].count(creates[0]['creationId']) == 1
        assert len([entry for entry in before['history']['entries'] if entry['operation'] == 'change_transactions']) == 1
    finally:
        reopened.close()


def test_explicit_fictional_provider_refresh_retains_correction_and_independent_manual_share(
    json_store, json_account, json_transaction, json_changes,
):
    json_store.save_item('item', 'fictional-ciphertext', 'Fictional Bank')
    incoming = json_transaction('provider-original', amount=-10000, pending=False,
                                merchant='  Fictional incoming payment  ')
    json_store.apply_sync('item', json_changes(added=[incoming]), 'first', [json_account(balance=25000)])
    json_store.edit_transaction('personal', 'provider-original', category_id='groceries',
                                reviewed=True, note='  Original provider memo\n')
    created = str(uuid4())
    json_store.change_transactions('personal', [{'transactionId': 'provider-original', 'amount': -6000}], [{
        'creationId': created, 'accountId': 'bank', 'date': incoming['date'], 'merchant': 'Fictional lender',
        'amount': -4000, 'currency': 'USD', 'categoryId': 'groceries',
    }], preserve_balances=True)
    before = {row['id']: row for row in json_store.export_budget('personal')['transactions']}
    assert json_store.accounts('personal')[0]['balance'] == 25000
    assert json_store.get_item('item')['cursor'] == 'first'
    json_store.apply_sync('item', json_changes(modified=[{**incoming, 'amount': -12000}]),
                          'second', [json_account(balance=27000)])
    after = {row['id']: row for row in json_store.export_budget('personal')['transactions']}
    assert after['provider-original']['amount'] == -6000
    assert after['provider-original']['original']['amount'] == -12000
    assert after['provider-original']['source'] == 'plaid'
    assert after['provider-original']['merchant'] == incoming['merchant']
    assert after['provider-original']['note'] == '  Original provider memo\n'
    assert after[created] == before[created]
    assert json_store.accounts('personal')[0]['balance'] == 27000
