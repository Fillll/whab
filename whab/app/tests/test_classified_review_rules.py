"""Three explicit types, review eligibility and auditable legacy preservation."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.errors import StoreError
from whab.plugins import LedgerService, review_status


@pytest.fixture(params=['sqlite','json'])
def classified(tmp_path, request):
    factory = SqliteStore if request.param == 'sqlite' else JsonStore
    store = factory(tmp_path / request.param, month='2026-11')
    ledger = LedgerService(store)
    accounts = [ledger.add_account('personal', {'name': name, 'type':'depository', 'balance':100000}) for name in ['Wallet 🍏','Кошелёк']]
    categories = {kind: ledger.add_category('personal', {'name':kind,'group':'家族','target':0, 'kind':kind}) for kind in ['expense','income','transfer']}
    transaction = ledger.add_transaction('personal', {'accountId':accounts[0],'date':'2026-11-13','merchant':'Fictional shop','amount':1234,'categoryId':None,'note':' [YNAB: cleared]\nexact  '})
    yield store, ledger, accounts, categories, transaction, factory
    store.close()


def command(ledger, operation, payload, *, revision=None, request=None):
    return ledger.command(operation, 'personal', payload, expected_revision=revision or ledger.revision,
        request_id=request or uuid4().hex, history={'actor':'Codex','actorKind':'agent','batchId':'review-fixture'})


def test_unclassified_review_rejects_and_classification_never_manufactures_review(classified):
    store, ledger, _, categories, tx, _ = classified
    before = store.export_budget()
    with pytest.raises(StoreError, match='Choose an Expense, Income or Transfer'):
        command(ledger, 'review_transactions', {'transactionIds':[tx],'reviewed':True})
    assert store.export_budget() == before
    for kind in ['expense','income','transfer']:
        command(ledger,'edit_transaction',{'transactionId':tx,'categoryId':categories[kind]})
        assert next(row for row in store.transactions('personal') if row['id']==tx)['reviewed'] is False
        command(ledger,'review_transactions',{'transactionIds':[tx],'reviewed':True})
        command(ledger,'edit_transaction',{'transactionId':tx,'categoryId':None})
        actual = next(row for row in store.transactions('personal') if row['id']==tx)
        assert actual['reviewed'] is False and actual['categoryId'] is None
        for field in ['id','accountId','date','merchant','amount','note','source']:
            assert actual[field] == before['transactions'][0][field]


def test_combined_classification_review_atomic_failure_and_exact_replay(classified):
    store, ledger, _, categories, tx, _ = classified
    revision, request = ledger.revision, uuid4().hex
    edits = {'edits':[{'transactionId':tx,'categoryId':categories['transfer'],'reviewed':True,'categoryAgent':'Codex'}]}
    generation = store.commit_evidence().generation
    saved = command(ledger,'edit_transactions',edits,revision=revision,request=request)
    assert saved['data']['changes'][0]['after']['reviewed'] is True
    assert command(ledger,'edit_transactions',edits,revision=revision,request=request) == saved
    assert store.commit_evidence().generation == generation + 1
    before = store.export_budget()
    with pytest.raises(StoreError, match='before marking reviewed'):
        command(ledger,'edit_transactions',{'edits':[{'transactionId':tx,'categoryId':None,'reviewed':True}]})
    assert store.export_budget() == before
    command(ledger,'edit_transactions',{'edits':[{'transactionId':tx,'categoryId':None}]})
    assert store.transactions('personal')[0]['reviewed'] is False


def test_transfer_type_is_explicit_and_matching_is_separate(classified):
    store, ledger, accounts, categories, tx, _ = classified
    command(ledger,'edit_transaction',{'transactionId':tx,'categoryId':categories['transfer'],'reviewed':True})
    row = store.transactions('personal')[0]
    assert row['isTransfer'] and 'transferPeerId' not in row
    peer = ledger.add_transaction('personal',{'accountId':accounts[1],'date':'2026-11-13','merchant':'Fictional incoming','amount':-1234,'categoryId':categories['transfer']})
    before = store.export_budget()
    summary = store.workspace('personal','2026-11')['summary']
    assert summary['spent'] == summary['income'] == 0
    assert all(row['categoryId'] != categories['transfer'] for row in store.workspace('personal','2026-11')['budget'])
    preview = store.preview_transfer_match('personal',[tx,peer],'match')
    assert store.export_budget() == before
    command(ledger,'match_transfer',{'transactionIds':[tx,peer],'action':'match','fingerprint':preview['fingerprint']})
    assert all(not row['reviewed'] for row in store.transactions('personal'))
    command(ledger,'review_transactions',{'transactionIds':[tx,peer],'reviewed':True})
    with pytest.raises(StoreError, match='Unmatch'):
        command(ledger,'classify_category',{'categoryId':categories['transfer'],'kind':'expense'})
    preview = store.preview_transfer_match('personal',[tx,peer],'unmatch')
    command(ledger,'match_transfer',{'transactionIds':[tx,peer],'action':'unmatch','fingerprint':preview['fingerprint']})
    assert all(row['categoryId']==categories['transfer'] and not row['reviewed'] for row in store.transactions('personal'))


def test_legacy_flags_read_verbatim_and_cleanup_is_explicit_reversible_when_valid(classified, tmp_path):
    source, _, _, categories, tx, factory = classified
    snapshot = source.export_budget()
    snapshot.pop('history',None)
    snapshot['transactions'][0].update(reviewed=True,isTransfer=True,inferredTransfer=True,merchant='  Fictional source  ')
    store = factory(tmp_path/'legacy',month='2026-11')
    try:
        store.restore_budget('personal',snapshot)
        original = deepcopy(store.export_budget())
        row = store.transactions('personal')[0]
        assert row['reviewed'] is True and not review_status(row,{r['id']:r for r in store.categories('personal')})['eligible']
        assert store.export_budget() == original
        ledger = LedgerService(store)
        command(ledger,'edit_transaction',{'transactionId':tx,'note':'Updated memo only'})
        assert store.transactions('personal')[0]['reviewed'] is True  # no implicit cleanup
        command(ledger,'review_transactions',{'transactionIds':[tx],'reviewed':False})
        assert store.transactions('personal')[0]['reviewed'] is False
        entry = store.budget_history('personal')['entries'][0]
        with pytest.raises(StoreError, match='invalid review'):
            store.history_preview('personal','undo',target=entry['id'])
        command(ledger,'edit_transaction',{'transactionId':tx,'categoryId':categories['income'],'categoryAgent':'Codex'})
        assert store.transactions('personal')[0]['reviewed'] is False
        command(ledger,'review_transactions',{'transactionIds':[tx],'reviewed':True})
        assert store.transactions('personal')[0]['merchant'] == '  Fictional source  '
    finally:
        store.close()


def test_pending_and_bank_hint_do_not_qualify():
    category = {'c': {'id':'c','kind':'transfer'}}
    assert review_status({'categoryId':None,'isTransfer':True},category)['eligible'] is False
    assert review_status({'categoryId':'c','pending':True},category)['eligible'] is False
    assert review_status({'deleted':True,'categoryId':'c'},category)['eligible'] is False
    assert review_status({'categoryId':'c'},category)['classification'] == 'transfer'
    ledger = LedgerService(SimpleNamespace())
    with pytest.raises(StoreError, match='Update the storage'):
        ledger.query('review_policy','personal')


@pytest.mark.parametrize('agent', [None, 'Codex'])
def test_classifying_legacy_review_never_creates_review_via_either_edit_route(classified, tmp_path, agent):
    source, _, _, categories, tx, factory = classified
    snapshot=source.export_budget()
    snapshot.pop('history',None)
    snapshot['transactions'][0].update(reviewed=True, merchant='  Fictional source  ')
    store=factory(tmp_path/'legacy-classification',month='2026-11')
    try:
        store.restore_budget('personal',snapshot)
        payload={'transactionId':tx,'categoryId':categories['transfer']}
        if agent is not None:
            payload['categoryAgent']=agent
        command(LedgerService(store),'edit_transaction',payload)
        row=store.transactions('personal')[0]
        assert row['categoryId']==categories['transfer'] and row['reviewed'] is False
        assert row['merchant']=='  Fictional source  '
    finally:
        store.close()
