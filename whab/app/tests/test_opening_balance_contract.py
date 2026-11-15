"""Opening Balance crosses public worker and authenticated HTTPS contracts."""
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from whab_plugin_server.plugin import plugin as server_plugin
from whab_plugin_sqlite import Store

from whab.plugins import (
    LedgerService,
    RequestConflict,
    RevisionConflict,
    StorageContext,
    StoreError,
)
from whab.plugins.remote_transport import RemoteStore
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker
from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router


def make_worker(kind, path):
    return StorageWorkerProxy(StorageWorker(kind), StorageContext(path, 'sandbox', {}), path / 'intents')


def seed(store, *, zero=False):
    source = Store(':memory:', month='2026-11')
    try:
        cash = source.add_account('personal', {'name': 'Fictional 貯蓄 🍏', 'type': 'depository', 'balance': 25000})
        category = source.add_category('personal', {'name': 'Existing opening', 'group': '家族', 'kind': 'income'})
        transaction = source.add_transaction('personal', {'accountId': cash, 'date': '2026-11-01', 'amount': -25000,
            'merchant': 'Fictional opening', 'categoryId': category, 'note': '  [YNAB: cleared]\nexact  '}, source='ynab')
        snapshot = source.export_budget()
        snapshot.pop('history', None)
        snapshot['transactions'][0]['merchant'] = '  Fictional opening  '
        if zero:
            snapshot['transactions'][0]['amount'] = 0
        store.restore_budget('personal', snapshot)
        return cash, category, transaction
    finally:
        source.close()


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_worker_opening_guarded_save_retry_restart_isolation_and_restoration(tmp_path, kind):
    path = tmp_path / 'fictional'
    store = make_worker(kind, path)
    try:
        cash, category, tx = seed(store)
        checkpoints = []
        service = LedgerService(store, budget_id='fictional', on_commit=lambda: checkpoints.append(store.export_budget_files()))
        assert service.query('opening_balance_policy', 'personal')['data'] == {
            'version': 1, 'categoryType': 'opening_balance', 'zeroAmountReview': True, 'preservesBalances': True}
        assert 'opening_balance' in service.query('review_policy', 'personal')['data']['types']
        before, demo = deepcopy(store.export_budget()), store.export_budget('demo')
        params = {'categoryId': category, 'kind': 'opening_balance'}
        preview = service.query('preview_category_classification', 'personal', parameters=params)['data']
        impact = next(row for row in preview['impacts'] if row['month'] == '2026-11' and row['currency'] == 'USD')
        assert impact['before']['income'] == impact['after']['openingFunding'] == 25000
        assert impact['after']['income'] == impact['before']['openingFunding'] == 0
        assert impact['before']['readyToAssign'] == impact['after']['readyToAssign']
        assert store.export_budget() == before and not checkpoints
        intent = {'request_id': str(uuid4()), 'expected_revision': service.revision,
                  'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-opening'}}
        result = service.command('classify_category', 'personal', params, **intent)
        assert len(checkpoints) == 1
        saved = store.export_budget()
        assert saved['transactions'] == before['transactions'] and saved['accounts'] == before['accounts']
        assert store.export_budget('demo') == demo
        assert service.command('classify_category', 'personal', params, **intent) == result
        with pytest.raises(RequestConflict):
            service.command('classify_category', 'personal', {**params, 'kind': 'expense'}, **intent)
        with pytest.raises(RevisionConflict):
            service.command('classify_category', 'personal', params, **{**intent, 'request_id': str(uuid4())})
        assert len(checkpoints) == 1
        store.close()
        store = make_worker(kind, path)
        restarted = LedgerService(store, budget_id='fictional', on_commit=lambda: pytest.fail('Replay cannot checkpoint'))
        assert restarted.command('classify_category', 'personal', params, **intent) == result
        restored = make_worker(kind, tmp_path / 'restored')
        try:
            restored.restore_budget_files('personal', checkpoints[0])
            assert restored.export_budget() == saved
            assert restored.workspace('personal', '2026-11')['summary']['openingFunding'] == 25000
        finally:
            restored.close()
    finally:
        store.close()


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_https_policy_classify_and_existing_zero_review(tmp_path, kind):
    store = make_worker(kind, tmp_path / 'https-fictional')
    cash, category, tx = seed(store, zero=True)
    snapshot = store.export_budget()
    service = LedgerService(store, budget_id='fictional')
    token = 'fictional-token-' + 'x' * 32
    binding = BoundRemoteInterface(server_plugin.interface.adapter,
        SimpleNamespace(config={'SERVER_TOKEN': token}, service=service, budget_id='fictional'))
    app = FastAPI()
    app.include_router(create_remote_router(lambda: binding))
    try:
        with TestClient(app, base_url='https://fictional.example') as server:
            def forward(request):
                return server.request(request.method, str(request.url), headers=request.headers, content=request.content)
            remote = RemoteStore('https://fictional.example', token, 'fictional', transport=httpx.MockTransport(forward))
            try:
                assert remote.supports_opening_balances()
                remote.classify_category('personal', category, 'opening_balance')
                assert remote.transactions('personal')[0]['reviewed'] is False
                remote.edit_transactions('personal', [{'transactionId': tx, 'reviewed': True}])
                row = remote.transactions('personal')[0]
                assert next(c for c in remote.categories('personal') if c['id'] == row['categoryId'])['kind'] == 'opening_balance' and row['reviewed'] is True and row['amount'] == 0
                assert row['merchant'] == '  Fictional opening  ' and row['note'] == snapshot['transactions'][0]['note']
                remote.edit_transaction('personal', tx, category_id=None)
                assert remote.transactions('personal')[0]['reviewed'] is False
            finally:
                remote.close()
    finally:
        store.close()


def test_unsupported_backend_refuses_opening_before_mutation_but_retains_ordinary_workflows(tmp_path, monkeypatch):
    store = Store(tmp_path / 'fictional.sqlite', month='2026-11')
    try:
        cash, category, tx = seed(store)
        ledger = LedgerService(store)
        monkeypatch.setattr(store, 'supports_opening_balances', lambda: False)
        before = store.export_budget()
        with pytest.raises(StoreError, match='Opening Balance'):
            ledger.query('opening_balance_policy', 'personal')
        assert ledger.query('review_policy', 'personal')['data']['types'] == ['expense', 'income', 'transfer']
        with pytest.raises(StoreError, match='Opening Balance'):
            ledger.add_category('personal', {'name': 'Opening', 'group': 'Finance', 'target': 0, 'kind': 'opening_balance'})
        with pytest.raises(StoreError, match='Opening Balance'):
            ledger.query('preview_category_classification', 'personal', parameters={'categoryId': category, 'kind': 'opening_balance'})
        assert store.export_budget() == before
        ledger.edit_transaction('personal', tx, note='Ordinary edit stays supported')
        monkeypatch.setattr(store, 'supports_opening_balances', lambda: True)
        ledger.command('classify_category', 'personal', {'categoryId': category, 'kind': 'opening_balance'},
                       request_id=str(uuid4()), expected_revision=ledger.revision)
        monkeypatch.setattr(store, 'supports_opening_balances', lambda: False)
        before = store.export_budget()
        with pytest.raises(StoreError, match='Opening Balance'):
            ledger.review_transactions('personal', [tx], True)
        assert store.export_budget() == before
    finally:
        store.close()


def test_remote_opening_capability_fails_closed_without_submitting_mutation():
    requests = []
    def unavailable(request):
        requests.append(request)
        return httpx.Response(404, json={'detail': 'Unknown query'})
    remote = RemoteStore('https://fictional.example', 'fictional-' + 'x' * 32, 'fictional', transport=httpx.MockTransport(unavailable))
    try:
        with pytest.raises(StoreError, match='no mutation was submitted'):
            remote.supports_opening_balances()
        assert all('/commands' not in str(row.url) for row in requests)
    finally:
        remote.close()


def test_older_remote_review_discovery_remains_usable_without_opening_policy():
    requested = []
    def older_authority(request):
        operation = request.url.path.rsplit('/', 1)[-1]
        requested.append(operation)
        if operation == 'review_policy':
            return httpx.Response(200, json={'budgetId': 'fictional', 'revision': 'fictional-old', 'data': {
                'version': 1, 'types': ['expense', 'income', 'transfer'],
                'classificationRequired': True, 'automaticLegacyCleanup': False}})
        return httpx.Response(404, json={'detail': 'Unknown ledger query.'})
    remote = RemoteStore('https://fictional.example', 'fictional-' + 'x' * 32, 'fictional',
                         transport=httpx.MockTransport(older_authority))
    try:
        service = LedgerService(remote)
        assert service.query('review_policy', 'personal')['data']['types'] == ['expense', 'income', 'transfer']
        assert requested == ['review_policy', 'opening_balance_policy', 'transfer_policy']
        with pytest.raises(StoreError, match='no mutation was submitted'):
            service.query('opening_balance_policy', 'personal')
    finally:
        remote.close()


@pytest.mark.parametrize('preview', [None, {}, {'changes': [{'path': ['categories', 'old'], 'before': None,
    'after': {'id': 'old', 'kind': 'opening_balance'}}]}])
def test_unsupported_history_without_portable_export_refuses_missing_proof_and_retained_opening(preview):
    writes = []
    backend = SimpleNamespace(supports_classified_review=lambda: True, categories=lambda _: [],
                              history_apply=lambda *args: writes.append(args))
    if preview is not None:
        backend.history_preview = lambda *args, **kwargs: deepcopy(preview)
    service = LedgerService(backend)
    with pytest.raises(StoreError, match='Update the storage'):
        service.command('history_apply', 'personal', {'action': 'redo', 'target': 'fictional-entry', 'fingerprint': 'a' * 64},
                        request_id=str(uuid4()), expected_revision=service.revision)
    assert writes == []


def test_unsupported_old_history_can_restore_proven_ordinary_changes_without_portable_export():
    writes = []
    backend = SimpleNamespace(supports_classified_review=lambda: True, categories=lambda _: [],
        history_preview=lambda *args, **kwargs: {'changes': [{'path': ['categories', 'ordinary'], 'before': None,
                                                            'after': {'id': 'ordinary', 'kind': 'income'}}]},
        history_apply=lambda *args: writes.append(args))
    service = LedgerService(backend)
    service.command('history_apply', 'personal', {'action': 'redo', 'target': 'fictional-entry', 'fingerprint': 'b' * 64},
                    request_id=str(uuid4()), expected_revision=service.revision)
    assert len(writes) == 1
