"""Browser category selections reuse atomic native edits, guards and durable receipts."""

from copy import deepcopy
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from whab_plugin_web.plugin import plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


@pytest.fixture(params=['sqlite', 'json'])
def category_client(request, tmp_path):
    context = StorageContext(tmp_path/'native', 'sandbox', {})
    store = StorageWorkerProxy(StorageWorker(request.param), context, tmp_path/'intents')
    account = store.add_account('personal', {'name':'Fictional 🍏 Счёт','type':'depository','balance':1000000})
    category = store.add_category('personal', {'name':'食料品','group':'Fictional Food','target':0})
    ids = [store.add_transaction('personal', {'accountId':account,'date':'2026-11-01','merchant':'  Fictional merchant  ','amount':amount,'categoryId':None,'note':'[YNAB]\nExisting note  '}) for amount in [100,-100,200]]
    def app_for(backend):
        settings = Settings(tmp_path/'ui'/uuid4().hex,5173,'sandbox','','','',False,'','',(),ui='web')
        return create_app(store=backend,settings=settings,plugins=PluginRegistry((plugin,)))
    app = app_for(store)
    try:
        with TestClient(app,base_url='https://127.0.0.1:5173') as client:
            yield client, store, ids, category, request.param, context, app_for
    finally:
        store.close()


def body(ids, category): return {'workspace':'personal','transactionIds':ids,'categoryId':category}


def test_atomic_selected_categories_preserve_fields_and_replay_after_restart(category_client, tmp_path):
    client, store, ids, category, kind, context, app_for = category_client
    before = deepcopy(store.export_budget())
    selection = body(ids[:2], category)
    preview = client.post('/api/transactions/categories/preview',json=selection)
    assert preview.status_code == 200
    assert store.export_budget()==before
    generation = store.commit_evidence().generation
    intent = {**selection,'revision':preview.json()['revision'],'requestId':uuid4().hex}
    saved = client.post('/api/transactions/categories',json=intent)
    assert saved.status_code==200 and saved.json()['data']['count']==2
    assert saved.json()['data']==preview.json()['data']
    assert store.commit_evidence().generation==generation+1
    assert client.post('/api/transactions/categories',json=intent).json()==saved.json()
    assert store.commit_evidence().generation==generation+1
    actual = store.export_budget()
    assert actual['accounts']==before['accounts'] and actual['assignments']==before['assignments']
    assert actual['transactions']==[{**row, 'categoryId':category,'categoryEdited':True} if row['id'] in ids[:2] else row for row in before['transactions']]
    assert store.budget_history('personal')['entries'][0]['operation']=='edit_transactions'
    assert store.budget_history('personal')['entries'][0]['changeCount']==2
    conflict = client.post('/api/transactions/categories',json={**intent,'categoryId':None})
    assert conflict.status_code==409 and conflict.json()['code']=='category_request_conflict'
    stale = client.post('/api/transactions/categories',json={**intent,'requestId':'new-stale'})
    assert stale.status_code==409 and stale.json()['code']=='category_revision_conflict'
    store.close()
    reopened = StorageWorkerProxy(StorageWorker(kind),context,tmp_path/'intents')
    try:
        with TestClient(app_for(reopened),base_url='https://127.0.0.1:5173') as fresh:
            assert fresh.post('/api/transactions/categories',json=intent).json()==saved.json()
            assert reopened.commit_evidence().generation==generation+1
            clear_preview = fresh.post('/api/transactions/categories/preview',json=body(ids[:2],None)).json()
            cleared = fresh.post('/api/transactions/categories',json={**body(ids[:2],None),'revision':clear_preview['revision'],'requestId':'clear-category'})
            assert cleared.status_code==200
            assert all(row['categoryId'] is None for row in reopened.transactions('personal'))
    finally:
        reopened.close()


def test_whole_selection_failure_rejects_missing_duplicate_invalid_and_oversized_targets(category_client):
    client, store, ids, category, *_ = category_client
    before = store.export_budget()
    revision = client.post('/api/transactions/categories/preview',json=body(ids,category)).json()['revision']
    for selection in [body([ids[0],'missing'],category),body([ids[0],ids[0]],category),body([],category),body(ids*34,category),body(ids,'missing-category'),{**body(ids,category),'reviewed':True},{**body(ids,category),'categoryId':4},{'workspace':'personal','transactionIds':ids}]:
        response = client.post('/api/transactions/categories/preview',json=selection)
        assert response.status_code==400
        response = client.post('/api/transactions/categories',json={**selection,'revision':revision,'requestId':uuid4().hex})
        assert response.status_code==400
        assert store.export_budget()==before
