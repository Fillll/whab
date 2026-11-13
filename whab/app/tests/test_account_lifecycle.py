"""Archive state preserves financial history and pauses only the addressed account."""

import copy

import pytest

from whab.domain.accounts import ACCOUNT_TYPES, normalize_account
from whab.plugins import LedgerService, StoreError, WorkspaceImport


@pytest.fixture(params=['sqlite', 'json'])
def lifecycle_store(request, tmp_path):
    if request.param == 'sqlite':
        from whab_plugin_sqlite import Store
        path = tmp_path / 'fictional.sqlite'
    else:
        from whab_plugin_json import Store
        path = tmp_path / 'fictional.json'
    store = Store(path, month='2026-10')
    opened = [store]

    def reopen():
        restored = Store(path, month='2026-10')
        opened.append(restored)
        return restored

    yield store, reopen
    for handle in opened:
        handle.close()


def account(identifier='cd', **changes):
    return {'id': identifier, 'name': 'Fictional ' + identifier, 'type': 'deposit', 'subtype': 'cd',
            'onBudget': False, 'balance': 120000, 'currency': 'USD', 'source': 'plaid',
            'institution': 'Fictional bank', 'itemId': 'item', 'mask': '1234', **changes}


def transaction(identifier, account_id='cd', **changes):
    return {'id': identifier, 'accountId': account_id, 'date': '2026-10-01', 'merchant': 'Fictional interest',
            'amount': -100, 'pending': False, 'suggestedCategoryId': None, 'source': 'plaid',
            'inferredTransfer': False, 'pendingTransactionId': None, **changes}


def delta(added=(), modified=(), removed=()):
    return {'added': list(added), 'modified': list(modified), 'removed': list(removed)}


def test_every_financial_type_can_close_reopen_without_changing_money_or_scope(lifecycle_store):
    store, _ = lifecycle_store
    for kind in ACCOUNT_TYPES:
        identifier = store.add_account('personal', {'name': 'Fictional '+kind, 'type': kind, 'balance': 12345})
        before = next(row for row in store.accounts('personal') if row['id'] == identifier)
        totals = store.workspace('personal', '2026-10')['summary']
        store.update_account('personal', identifier, {'closed': True})
        archived = next(row for row in store.accounts('personal') if row['id'] == identifier)
        assert archived == {**before, 'closed': True}
        assert store.workspace('personal', '2026-10')['summary'] == totals
        with pytest.raises(StoreError, match='Reopen'):
            store.add_transaction('personal', {'accountId': identifier, 'date': '2026-10-01', 'merchant': 'No new entry', 'amount': 1, 'categoryId': None})
        store.update_account('personal', identifier, {'closed': False})
        assert next(row for row in store.accounts('personal') if row['id'] == identifier) == before
    assert store.supports_account_lifecycle() is True


def test_imported_cd_unknown_balance_history_and_portable_roundtrip(lifecycle_store, tmp_path):
    store, reopen = lifecycle_store
    imported = WorkspaceImport(accounts=({'id':'imported','name':'🐖 Fictional CD','institution':'YNAB export','type':'other','balance':54321,'source':'ynab','stale':True,'balanceUnknown':True},),
        categories=(),transactions=({'id':'old','accountId':'imported','date':'2026-10-01','merchant':'Fictional bank','amount':-54321,'categoryId':None,'pending':False,'reviewed':True,'source':'ynab','note':'Keep annotation','isTransfer':False,'inferredTransfer':False,'categoryEdited':True,'deleted':False},),assignments=(),budget_start_month='2026-10')
    store.import_workspace('personal','ynab','fictional-fingerprint',imported)
    before = copy.deepcopy(store.transactions('personal'))
    store.update_account('personal','imported',{'type':'deposit','subtype':'cd','onBudget':False})
    store.update_account('personal','imported',{'closed':True})
    row=store.accounts('personal')[0]
    assert row['closed'] and row['balanceUnknown'] and row['stale'] and row['balance']==54321
    assert store.transactions('personal') == before
    assert store.workspace('personal','2026-10')['summary']['readyToAssign'] == 0
    exported=store.export_budget()
    files=store.export_budget_files()
    assert exported['accounts'][0]['closed']
    assert 'closedAccountCursors' not in str(exported)
    store.close()
    opened=reopen()
    try:
        assert opened.accounts('personal')[0]['closed']
        assert opened.transactions('personal') == before
    finally:
        opened.close()
    # Both generic financial and owning portable formats preserve archive state.
    for binary in (False, True):
        restored = type(store)(tmp_path / ('restored-files' if binary else 'restored-financial'), month='2026-10')
        try:
            if binary:
                restored.restore_budget_files('personal', files)
            else:
                restored.restore_budget('personal', exported)
            assert restored.accounts('personal')[0]['closed']
            assert restored.transactions('personal') == before
        finally:
            restored.close()


def test_closed_bank_account_is_frozen_while_peer_syncs_and_reopen_replays_checkpoint(lifecycle_store):
    from whab.plugins.storage_transfer import decode_local_state

    store,reopen=lifecycle_store
    store.save_item('item','fictional-encrypted-token','Fictional bank')
    records=[account(), account('peer',type='depository',subtype='checking',onBudget=True)]
    store.apply_sync('item',delta(added=[transaction('old'), transaction('peer-old','peer')]),'cursor-1',records)
    store.configure_bank_sync('item',records,{'cd':{'accountId':'cd','since':'2026-01-01'},'peer':{'accountId':'peer','since':None}})
    store.apply_sync('item',delta(),'cursor-2',records)
    store.edit_transaction('personal','old','groceries',True,'Keep my note')
    store.update_account('personal','cd',{'closed':True})
    frozen=next(row for row in store.accounts('personal') if row['id']=='cd')
    old=next(row for row in store.transactions('personal') if row['id']=='old')
    links=copy.deepcopy(store.get_item('item')['syncSettings'])
    assert store.get_item('item')['closedAccountCursors']=={'cd':'cursor-2'}
    store.apply_sync('item',delta(added=[transaction('skipped'),transaction('peer-new','peer')],modified=[transaction('old',amount=-999)],removed=['old']),
                     'cursor-3',[account(balance=999999),records[1]])
    assert next(row for row in store.accounts('personal') if row['id']=='cd')==frozen
    assert next(row for row in store.transactions('personal') if row['id']=='old')==old
    assert 'skipped' not in {row['id'] for row in store.transactions('personal')}
    assert 'peer-new' in {row['id'] for row in store.transactions('personal')}
    assert store.get_item('item')['cursor']=='cursor-3'
    assert store.get_item('item')['syncSettings']==links
    # Repeat close never replaces the original checkpoint with a newer peer cursor.
    store.update_account('personal','cd',{'closed':True})
    store.save_item('item','fictional-refreshed-token','Fictional bank')
    assert store.get_item('item')['closedAccountCursors']=={'cd':'cursor-2'}
    # Resume intent survives both native protected copies and process restarts.
    snapshot = store.export_local()
    copied = decode_local_state(snapshot.financial, snapshot.protected)
    assert copied['personal']['items']['item']['closedAccountCursors'] == {'cd':'cursor-2'}
    assert copied['personal']['accounts']['cd']['closed']
    store.close()
    store = reopen()
    assert store.get_item('item')['closedAccountCursors'] == {'cd':'cursor-2'}
    assert next(row for row in store.accounts('personal') if row['id']=='cd') == frozen
    store.update_account('personal','cd',{'closed':False})
    assert store.get_item('item')['cursor']=='cursor-2'
    assert 'closedAccountCursors' not in store.get_item('item')
    store.apply_sync('item',delta(added=[transaction('skipped'),transaction('peer-new','peer')]),'cursor-4',records)
    assert len([row for row in store.transactions('personal') if row['id']=='peer-new'])==1
    assert 'skipped' in {row['id'] for row in store.transactions('personal')}
    assert not next(row for row in store.accounts('personal') if row['id']=='cd').get('closed',False)


def test_closed_destinations_keep_existing_mapping_but_reject_new_links(lifecycle_store):
    store,_ = lifecycle_store
    store.save_item('item','fictional-encrypted-token','Fictional bank')
    records = [account()]
    store.apply_sync('item',delta(),'cursor-1',records)
    selection = {'cd': {'accountId':'cd','since':None}}
    store.configure_bank_sync('item',records,selection)
    store.update_account('personal','cd',{'closed':True})
    before = store.accounts('personal')
    store.configure_bank_sync('item',[account(balance=999999)],selection)
    assert store.accounts('personal') == before
    assert store.get_item('item')['syncSettings'] == selection
    target = store.add_account('personal',{'name':'Fictional archived destination','type':'deposit','subtype':'cd','balance':100})
    store.update_account('personal',target,{'closed':True})
    evidence = store.commit_evidence()
    with pytest.raises(StoreError,match='Reopen'):
        store.configure_bank_sync('item',records,{'cd': {'accountId':target,'since':None}})
    assert store.commit_evidence() == evidence


def test_overlapping_archives_reopen_without_skipping_an_earlier_resume_point(lifecycle_store):
    store,_ = lifecycle_store
    store.save_item('item','fictional-encrypted-token','Fictional bank')
    records = [account(),account('peer')]
    store.apply_sync('item',delta(),'cursor-1',records)
    store.update_account('personal','cd',{'closed':True})
    store.apply_sync('item',delta(),'cursor-2',records)
    store.update_account('personal','peer',{'closed':True})
    store.update_account('personal','cd',{'closed':False})
    assert store.get_item('item')['cursor'] is None
    store.update_account('personal','peer',{'closed':False})
    assert store.get_item('item')['cursor'] is None
    assert 'closedAccountCursors' not in store.get_item('item')
    store.apply_sync('item',delta(added=[transaction('during-first-pause'),transaction('during-second-pause','peer')]),'cursor-3',records)
    assert {row['id'] for row in store.transactions('personal')} == {'during-first-pause','during-second-pause'}


def test_history_preview_does_not_reopen_or_mutate_resume_cursor(lifecycle_store):
    store,_=lifecycle_store
    store.save_item('item','fictional-private-token','Fictional bank')
    store.apply_sync('item', delta(), 'before-close', [account()])
    service=LedgerService(store)
    service.update_account('personal','cd',{'closed':True})
    target=store.budget_history('personal')['entries'][0]['id']
    # Native application sees no financial changes for an archived account.
    store.apply_sync('item',delta(),'peer-progress',[account(balance=999999)])
    before=copy.deepcopy(store.get_item('item'))
    preview=store.history_preview('personal','undo',target)
    assert store.accounts('personal')[0]['closed']
    assert store.get_item('item')==before
    store.history_apply('personal','undo',target,preview['fingerprint'])
    assert not store.accounts('personal')[0].get('closed',False)
    assert store.get_item('item')['cursor']=='before-close'
    assert 'closedAccountCursors' not in store.get_item('item')



@pytest.mark.parametrize('value',[None,0,1,'closed',[]])
def test_invalid_lifecycle_is_rejected_by_the_domain(value):
    with pytest.raises(StoreError,match='lifecycle'):
        normalize_account({'type':'deposit','onBudget':False,'subtype':'cd','closed':value})


def test_false_lifecycle_default_keeps_legacy_canonical_records_and_hashes():
    legacy=normalize_account({'type':'credit','onBudget':True,'subtype':'credit_card'})
    assert 'closed' not in legacy
    assert normalize_account({**legacy,'closed':False})==legacy


def test_older_storage_rejects_archive_mutation_before_writing():
    from types import SimpleNamespace
    for support in [None,lambda:False]:
        store=SimpleNamespace(**({'supports_account_lifecycle':support} if support else {}))
        service=LedgerService(store)
        with pytest.raises(StoreError,match='storage plugin'):
            service.update_account('personal','fictional',{'closed':True})
