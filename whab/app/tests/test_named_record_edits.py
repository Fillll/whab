"""Name-only and memo-only public commands preserve fictional financial records."""

from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from whab_plugin_json import Store as JsonStore
from whab_plugin_sqlite import Store as SqliteStore

from whab.domain.categories import rename_category
from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService, RequestConflict, RevisionConflict


@pytest.fixture(params=['sqlite', 'json'])
def ledger(tmp_path, request):
    store = (SqliteStore(':memory:', month='2026-11') if request.param == 'sqlite'
             else JsonStore(tmp_path / 'fictional.json', month='2026-11'))
    service = LedgerService(store)
    account = service.add_account('personal', {'name':'Fictional checking','type':'depository','balance':1000000})
    category = service.add_category('personal', {'name':'Rent','group':'House','target':1234})
    transaction = service.add_transaction('personal', {'accountId':account,'merchant':'Fictional shop',
        'date':'2026-11-01','amount':12345,'categoryId':category,'note':'  [YNAB: uncleared]\noriginal memo  '})
    service.edit_transaction('personal', transaction, reviewed=True)
    service.assign('personal', category, '2026-11', 10000)
    yield store, service, category, transaction
    store.close()


def snapshot(store):
    value = store.export_budget('personal')
    return {**value, 'categories': {row['id']: row for row in value['categories']},
            'transactions': {row['id']: row for row in value['transactions']}}


def invoke(service, operation, payload, *, request_id=None, revision=None):
    return service.command(operation, 'personal', payload, request_id=request_id or uuid4().hex,
        expected_revision=revision or service.revision, history={'actor':'Codex','actorKind':'agent','batchId':'fictional-cleanup'})


def test_category_rename_preserves_identity_assignments_and_replay(ledger):
    store, service, category, transaction = ledger
    before = deepcopy(snapshot(store))
    revision, request = service.revision, uuid4().hex
    args = {'categoryId':category,'name':'Main Residence'}
    first = invoke(service, 'rename_category', args, request_id=request, revision=revision)
    after = snapshot(store)
    expected = deepcopy(before)
    expected['categories'][category]['name'] = 'Main Residence'
    expected['history'] = after['history']
    assert after == expected
    assert after['transactions'][transaction]['categoryId'] == category
    assert invoke(service,'rename_category',args,request_id=request,revision=revision) == first
    assert snapshot(store) == after
    entry = store.budget_history('personal')['entries'][0]
    assert entry['operation'] == 'rename_category' and entry['actor'] == 'Codex' and entry['batchId'] == 'fictional-cleanup'
    assert entry['reversible'] is True and entry['changeCount'] == 1
    with pytest.raises(RevisionConflict):
        invoke(service,'rename_category',{'categoryId':category,'name':'Stale'},revision=revision)
    service.add_category('personal', {'name':'Reserved','group':'House','target':0})
    before = snapshot(store)
    with pytest.raises(StoreError, match='already exists'):
        invoke(service,'rename_category',{'categoryId':category,'name':' reserved '})
    assert snapshot(store) == before


def test_append_preserves_verbatim_text_fields_and_replays_once(ledger):
    store, service, category, transaction = ledger
    before = deepcopy(snapshot(store))
    revision, request = service.revision, uuid4().hex
    args = {'transactionId':transaction,'text':"Alex's matress  \n"}
    first = invoke(service,'append_transaction_note',args,request_id=request,revision=revision)
    after = snapshot(store)
    expected = deepcopy(before)
    expected['transactions'][transaction]['note'] += "\nAlex's matress  \n"
    expected['history'] = after['history']
    assert after == expected
    assert invoke(service,'append_transaction_note',args,request_id=request,revision=revision) == first
    assert snapshot(store) == after
    with pytest.raises(RequestConflict):
        invoke(service,'append_transaction_note',{'transactionId':transaction,'text':'Different'},request_id=request,revision=revision)
    with pytest.raises(RevisionConflict):
        invoke(service,'append_transaction_note',args,revision=revision)
    entry = store.budget_history('personal')['entries'][0]
    assert entry['operation'] == 'append_transaction_note' and entry['reversible'] is True
    assert entry['changeCount'] == 1 and entry['actorKind'] == 'agent'
    before = snapshot(store)
    for text in ['', 'x'*1000, None]:
        with pytest.raises(StoreError):
            invoke(service,'append_transaction_note',{'transactionId':transaction,'text':text})
        assert snapshot(store) == before
    with pytest.raises(StoreError, match='not found'):
        invoke(service,'append_transaction_note',{'transactionId':'absent','text':'Text'})


def test_new_commands_refuse_backends_without_history_or_rename_contract():
    with pytest.raises(StoreError, match='audit history'):
        invoke(LedgerService(SimpleNamespace()),'append_transaction_note',{'transactionId':'fictional','text':'Text'})
    backend = SimpleNamespace(history_scope=lambda *_: None, budget_history=lambda *_: None)
    with pytest.raises(StoreError, match='rename categories'):
        invoke(LedgerService(backend),'rename_category',{'categoryId':'fictional','name':'New'})


def test_missing_category_and_empty_note():
    with pytest.raises(StoreError, match='Category not found'):
        rename_category({'categories':{}}, 'missing', 'New')
    store = SqliteStore(':memory:')
    try:
        service = LedgerService(store)
        account = service.add_account('personal',{'name':'Fictional','type':'depository','balance':1000})
        transaction = service.add_transaction('personal',{'accountId':account,'date':'2026-11-01','merchant':'Shop','amount':100,'categoryId':None})
        invoke(service,'append_transaction_note',{'transactionId':transaction,'text':'  exact  '})
        assert service.transactions('personal')[0]['note'] == '  exact  '
    finally:
        store.close()
