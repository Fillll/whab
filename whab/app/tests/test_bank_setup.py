"""Account destinations, history cutoffs and native persistence use fictional data."""

from copy import deepcopy
from uuid import uuid4

import pytest
from whab_plugin_json.store import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from tests.storage.test_bank_boundaries import account, changes, transaction
from whab.domain.bank_setup import validate_sync_settings
from whab.domain.errors import StoreError
from whab.plugins.storage_transfer import (
    StorageOperation,
    canonical_storage_json,
    read_storage_json,
)


@pytest.fixture(params=['sqlite', 'json'])
def ledger(tmp_path, request):
    backend = SqliteStore(tmp_path / 'bank.sqlite', environment='sandbox') if request.param == 'sqlite' else JsonStore(tmp_path / 'bank.json')
    backend.save_item('item', 'fictional-encrypted-token', 'Fictional bank')
    yield backend
    backend.close()


def configure(ledger, accounts, selection):
    before = ledger.commit_evidence()
    operation = StorageOperation(uuid4().hex, before.authority_epoch, before.generation, 'personal', 'configure_bank_sync',
        canonical_storage_json({'itemId': 'item', 'accounts': accounts, 'selection': selection}))
    result = ledger.execute_operation(operation)
    assert ledger.execute_operation(operation).receipt == result.receipt
    return read_storage_json(result.receipt.result)


def test_link_existing_account_preserves_history_annotations_and_replays_cutoff(ledger):
    target = ledger.add_account('personal', {'name': 'My checking', 'type': 'depository', 'balance': 0})
    ledger.add_transaction('personal', {'id': 'manual-history', 'accountId': target, 'date': '2026-09-30', 'merchant': 'History', 'amount': 12, 'categoryId': None, 'note': 'Keep'})
    plan = {'account': {'accountId': target, 'since': '2026-10-02'}}
    configure(ledger, [account()], plan)
    added = [transaction('old', date='2026-10-01'), transaction('new', date='2026-10-02')]
    result = ledger.apply_sync('item', changes(added), 'one', [account()])
    assert result['added'] == 1
    rows = ledger.transactions('personal')
    assert {row['id'] for row in rows} == {'manual-history', 'new'}
    assert {row['accountId'] for row in rows} == {target}
    assert ledger.accounts('personal')[0]['name'] == 'My checking'
    assert ledger.get_item('item')['syncSettings'] == plan
    ledger.edit_transaction('personal', 'new', note='Reviewed annotation', reviewed=True)
    ledger.apply_sync('item', changes(modified=[transaction('new', date='2026-10-01')]), 'two', [account()])
    saved = next(row for row in ledger.transactions('personal') if row['id'] == 'new')
    assert saved['note'] == 'Reviewed annotation' and saved['reviewed']
    configure(ledger, [account()], { 'account': {'accountId': target, 'since': None}})
    assert ledger.get_item('item')['cursor'] is None
    ledger.apply_sync('item', changes(added), 'three', [account()])
    assert len(ledger.transactions('personal')) == 3
    evidence = ledger.export_local()
    assert evidence is not None  # protected transfers retain the account selection.


def test_existing_auto_created_bank_account_can_be_joined_without_losing_annotations(ledger):
    ledger.apply_sync('item', changes([transaction()]), 'one', [account()])
    ledger.edit_transaction('personal', 'transaction', note='Keep bank note', reviewed=True)
    target = ledger.add_account('personal', {'name': 'Existing imported account', 'type': 'depository', 'balance': 0})
    result = configure(ledger, [account()], {'account': {'accountId': target, 'since': None}})
    assert result['retiredAccounts'] == ['account']
    assert [row['id'] for row in ledger.accounts('personal')] == [target]
    row = ledger.transactions('personal')[0]
    assert row['accountId'] == target and row['note'] == 'Keep bank note' and row['reviewed']
    ledger.apply_sync('item', changes(modified=[transaction()]), 'two', [account()])
    assert ledger.transactions('personal')[0]['accountId'] == target
    with pytest.raises(StoreError, match='cannot be changed'):
        configure(ledger, [account()], {'account': {'accountId': 'elsewhere', 'since': None}})
    # Exchanging an update token retains configured destinations.
    ledger.save_item('item', 'fictional-refreshed-encrypted-token', 'Fictional bank')
    assert ledger.get_item('item')['syncSettings']['account']['accountId'] == target


def test_unselected_accounts_never_import_and_pending_annotations_follow_posting(ledger):
    configure(ledger, [account(), account('skipped')], {'account': {'accountId': 'account', 'since': '2026-10-02'}})
    ledger.apply_sync('item', changes([transaction('pending', pending=True, date='2026-10-02'), transaction('skip', account_id='skipped')]), 'one', [account(), account('skipped')])
    ledger.edit_transaction('personal', 'pending', note='Pending note')
    ledger.apply_sync('item', changes([transaction('posted', pendingTransactionId='pending', date='2026-10-01')]), 'two', [account()])
    row = next(row for row in ledger.transactions('personal') if row['id'] == 'posted')
    assert row['note'] == 'Pending note'
    assert {row['id'] for row in ledger.accounts('personal')} == {'account'}
    assert all(row['id'] != 'skip' for row in ledger.transactions('personal'))


@pytest.mark.parametrize('selection,remote', [
    ({}, [account()]),
    ({'missing': {'accountId': 'missing', 'since': None}}, [account()]),
    ({'account': {'accountId': 'missing', 'since': None}}, [account()]),
    ({'account': {'accountId': 'account', 'since': None}}, [account(item_id='other')]),
    ({'account': {'accountId': 'account', 'since': None}}, [account(source='other')]),
])
def test_bad_configuration_leaves_native_state_unchanged(ledger, selection, remote):
    before = ledger.get_item('item')
    evidence = ledger.commit_evidence()
    with pytest.raises(StoreError):
        configure(ledger, remote, selection)
    assert ledger.get_item('item') == before and ledger.commit_evidence() == evidence


def test_currency_and_foreign_connection_destinations_are_rejected(ledger):
    target = ledger.add_account('personal', {'name': 'Euro', 'type': 'depository', 'balance': 0, 'currency': 'EUR'})
    with pytest.raises(StoreError, match='currencies'):
        configure(ledger, [account()], {'account': {'accountId': target, 'since': None}})
    ledger.save_item('foreign', 'fictional-other-token', 'Other')
    ledger.apply_sync('foreign', changes(), 'one', [account('foreign-account', item_id='foreign')])
    with pytest.raises(StoreError, match='another bank connection'):
        configure(ledger, [account()], {'account': {'accountId': 'foreign-account', 'since': None}})


@pytest.mark.parametrize('value', [None, [], {'': {}}, {'a': {}}, {'a': {'accountId': 'b', 'since': 'invalid'}},
    {'a': {'accountId': 'b', 'since': None}, 'c': {'accountId': 'b', 'since': None}}])
def test_invalid_account_settings(value):
    with pytest.raises(StoreError):
        validate_sync_settings(deepcopy(value))


def test_deselecting_an_account_retains_its_history_and_ignores_later_changes(ledger):
    accounts = [account(), account('other')]
    configure(ledger, accounts, {key: {'accountId': key, 'since': None} for key in ('account', 'other')})
    ledger.apply_sync('item', changes([transaction('first'), transaction('second', account_id='other')]), 'one', accounts)
    configure(ledger, accounts, {'account': {'accountId': 'account', 'since': None}})
    result = ledger.apply_sync('item', changes(modified=[transaction('second', account_id='other', amount=999)], removed=['second']), 'two', accounts)
    assert result['modified'] == result['removed'] == 0
    second = next(row for row in ledger.transactions('personal') if row['id'] == 'second')
    assert not second['deleted'] and second['amount'] == 1000
