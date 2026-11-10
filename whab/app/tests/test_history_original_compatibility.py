"""Read old imported agent edits without rewriting their permanent audit chain."""

from copy import deepcopy
from uuid import uuid4

import pytest

from whab.domain import commands
from whab.plugins import LedgerService, StorageContext, WorkspaceImport
from whab.plugins.runtime import load_plugins
from whab.plugins.storage import open_storage
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


def imported():
    return WorkspaceImport(
        accounts=({'id':'fictional-account','name':'Fictional imported','institution':'Export',
                   'type':'other','balance':1000,'source':'ynab','stale':True,'balanceUnknown':True},),
        categories=({'id':'fictional-home','name':'Home','group':'Fictional maintenance','target':0},
                    {'id':'fictional-destination','name':'Destination','group':'Fictional work','target':0}),
        transactions=({'id':'fictional-transaction','accountId':'fictional-account','date':'2026-08-01',
                       'merchant':'Fictional merchant','amount':-100,'categoryId':'fictional-home',
                       'pending':True,'reviewed':False,'source':'ynab','note':'Retain annotation',
                       'isTransfer':False,'inferredTransfer':False,'categoryEdited':False,'deleted':False},),
        assignments=({'month':'2026-08','categoryId':'fictional-home','assigned':0},),
        budget_start_month='2026-08')


def edit(service, category, intent):
    return service.command('edit_transaction','personal',{'transactionId':'fictional-transaction',
        'categoryId':category,'categoryAgent':'Codex'},request_id=intent,expected_revision=service.revision)


@pytest.mark.parametrize('kind', ['sqlite','json'])
def test_legacy_empty_original_survives_native_worker_history_reopen_and_portable(kind, tmp_path, monkeypatch):
    context = StorageContext(tmp_path, 'sandbox', {})
    descriptor = load_plugins((kind,), _worker=True).storage_plugin(kind)
    native = open_storage(descriptor, context)
    try:
        native.import_workspace('personal','ynab','a'*64,imported())
        fixed = commands.edit_transaction_details
        def legacy(state, identifier, changes):
            fixed(state,identifier,changes)
            state['transactions'][identifier]['original'] = {}
        # Reproduce the prior shipped implementation through its public write,
        # not by modifying a database, a history hash or a runtime file.
        with monkeypatch.context() as patch:
            patch.setattr(commands,'edit_transaction_details',legacy)
            edit(LedgerService(native),'fictional-destination','fictional-legacy')
        before = native.export_budget('personal')
        journal = deepcopy(before['history'])
        assert before['transactions'][0]['original'] == {}
        evidence = native.commit_evidence()
        assert native.budget_history('personal',limit=1)['total'] == 2
        target = native.budget_history('personal')['entries'][0]['id']
        assert native.budget_history('personal',target=target)['entries'][0]['changes'][0]['after']['original'] == {}
        assert native.commit_evidence() == evidence and native.export_budget('personal') == before
        files = native.export_budget_files()
    finally:
        native.close()
    proxy = StorageWorkerProxy(StorageWorker(kind), context, tmp_path/'fictional-intents')
    try:
        service = LedgerService(proxy)
        page = service.query('history','personal',parameters={'limit':1})['data']
        assert page['total'] == 2 and page['next']
        assert service.query('history','personal',parameters={'before':page['next'],'limit':1})['data']['entries']
        assert proxy.export_budget('personal') == before
        assert proxy.category_deletion_status('personal','fictional-home')['assignments']['zeroMonths'] == ['2026-08']
        edit(service,'fictional-home','fictional-fixed')
        saved = proxy.export_budget('personal')
        assert 'original' not in saved['transactions'][0]
        assert saved['history']['entries'][:len(journal['entries'])] == journal['entries']
        entry = proxy.budget_history('personal')['entries'][0]['id']
        for action in ['undo','redo']:
            preview = service.query('history_preview','personal',parameters={'action':action,'target':entry})
            service.command('history_apply','personal',{k:v for k,v in preview['data'].items() if k!='changes'},
                            request_id=uuid4().hex,expected_revision=preview['revision'])
        final = proxy.export_budget('personal')
        assert final['transactions'] == saved['transactions']
        assert final['assignments'] == before['assignments'] and final['accounts'] == before['accounts']
        assert final['history']['entries'][:len(journal['entries'])] == journal['entries']
    finally:
        proxy.close()
    restored = open_storage(descriptor, StorageContext(tmp_path/'portable', 'sandbox', {}))
    try:
        restored.restore_budget_files('personal',files)
        assert restored.budget_history('personal')['total'] == 2
        assert restored.export_budget('personal')['history'] == journal
    finally:
        restored.close()
