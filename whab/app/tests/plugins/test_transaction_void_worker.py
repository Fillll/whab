"""Voids cross the public host, real worker, durable receipt and portable boundary."""

from copy import deepcopy

import pytest

from whab.domain.accounts import normalize_account
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.plugins import (
    LedgerService,
    RequestConflict,
    RevisionConflict,
    StorageContext,
    StoreError,
)
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


def fixture_state(count=100):
    state = initial_state('personal', '2026-11')
    state['accounts'] = {key: normalize_account({
        'id': key, 'name': name, 'type': kind, 'balance': 1000000,
        'currency': 'USD', 'source': 'manual', 'institution': '', 'stale': False,
        'balanceUnknown': False,
    }) for key, name, kind in [('cash', '旅行 🍏', 'depository'), ('credit', 'Сбережения', 'credit')]}
    state['transactions'] = {}
    for index in range(count):
        identifier = f'fictional-{index}'
        state['transactions'][identifier] = {
            'id': identifier, 'accountId': 'cash' if index % 2 else 'credit',
            'date': '2026-11-01', 'merchant': '  Fictional 商店  ', 'merchantLogo': None,
            'amount': 0 if index == 0 else (100 + index) * (-1 if index % 3 else 1),
            'categoryId': None, 'pending': False, 'reviewed': False,
            'source': 'ynab' if index % 2 == 0 else 'manual',
            'note': '[YNAB]  \nPreserved', 'isTransfer': False, 'inferredTransfer': False,
            'categoryEdited': True, 'deleted': False, 'categoryAgent': 'Earlier agent',
        }
    return state


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_hundred_voids_one_commit_exact_restart_receipt_and_portable_reopen(kind, tmp_path):
    context = StorageContext(tmp_path / 'fictional', 'sandbox', {})
    intents = tmp_path / 'intents'
    store = StorageWorkerProxy(StorageWorker(kind), context, intents)
    saves = []
    identifiers = list(fixture_state()['transactions'])
    try:
        store.restore_budget('personal', portable_snapshot(fixture_state()))
        service = LedgerService(store, budget_id='fictional', on_commit=lambda: saves.append(store.export_budget_files()))
        original = deepcopy(store.export_budget('personal'))
        assert service.query('transaction_void_policy', 'personal')['data'] == {
            'version': 1, 'maxTransactions': 100, 'durableTombstones': True,
        }
        preview = service.query('preview_void_transactions', 'personal', parameters={'transactionIds': identifiers})
        assert store.export_budget('personal') == original and not saves
        generation = store.commit_evidence().generation
        payload = {'transactionIds': identifiers, 'fingerprint': preview['data']['fingerprint']}
        args = {'request_id': 'fictional-void', 'expected_revision': preview['revision'],
                'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-voids'}}
        saved = service.command('void_transactions', 'personal', payload, **args)
        assert saved['data'] == preview['data'] and len(saves) == 1
        assert store.commit_evidence().generation == generation + 1
        assert service.command('void_transactions', 'personal', payload, **args) == saved
        assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
        with pytest.raises(RequestConflict):
            service.command('unvoid_transactions', 'personal', payload, **args)
        with pytest.raises(RevisionConflict):
            service.command('void_transactions', 'personal', payload, **{**args, 'request_id': 'stale'})
        excluded = store.export_budget('personal')
        assert store.transactions('personal') == []
        originals = {row['id']: row for row in original['transactions']}
        assert len(excluded['transactions']) == 100
        for row in excluded['transactions']:
            assert row['voided'] and row['deleted'] and not row['reviewed']
            assert {key: value for key, value in row.items() if key not in {'voided', 'deleted', 'reviewed'}} == {
                key: value for key, value in originals[row['id']].items() if key not in {'deleted', 'reviewed'}
            }
        assert excluded['assignments'] == original['assignments']
        history = store.budget_history('personal')['entries'][0]
        assert history['actor'] == 'Codex' and history['batchId'] == 'fictional-voids'
    finally:
        store.close()
    reopened = StorageWorkerProxy(StorageWorker(kind), context, intents)
    restored = StorageWorkerProxy(StorageWorker(kind), StorageContext(tmp_path / 'empty', 'sandbox', {}), tmp_path / 'empty-intents')
    try:
        service = LedgerService(reopened, budget_id='fictional', on_commit=lambda: saves.append(reopened.export_budget_files()))
        assert service.command('void_transactions', 'personal', payload, **args) == saved
        assert len(saves) == 1 and reopened.commit_evidence().generation == generation + 1
        restored.restore_budget_files('personal', saves[0])
        assert restored.export_budget('personal')['transactions'] == excluded['transactions']
        for action in ('undo', 'redo'):
            plan = service.query('history_preview', 'personal', parameters={'action': action, 'target': history['id']})
            service.command('history_apply', 'personal', {key: plan['data'][key] for key in ('action', 'target', 'fingerprint')},
                            request_id='history-' + action, expected_revision=plan['revision'])
        plan = service.query('preview_unvoid_transactions', 'personal', parameters={'transactionIds': identifiers})
        service.command('unvoid_transactions', 'personal', {'transactionIds': identifiers, 'fingerprint': plan['data']['fingerprint']},
                        request_id='fictional-unvoid', expected_revision=plan['revision'])
        final = reopened.export_budget('personal')
        assert final['accounts'] == original['accounts']
        assert {row['id']: row for row in final['transactions']} == {key: {**row, 'voided': False} for key, row in originals.items()}
    finally:
        reopened.close()
        restored.close()


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_real_saved_container_void_and_unvoid_each_checkpoint_once(kind, tmp_path, monkeypatch):
    from whab_plugin_json import Store as JSONStore
    from whab_plugin_sqlite import Store as SQLiteStore

    from whab.budgets.container import decode_container
    from whab.budgets.session import BudgetSession, TrackedStorage

    factory = SQLiteStore if kind == 'sqlite' else JSONStore
    session = BudgetSession.create(tmp_path / 'runtime', 'sandbox', 'Fictional voids', storage=kind, path=tmp_path / 'fictional.whab')
    native = factory(session.storage_home / ('ledger.sqlite' if kind == 'sqlite' else 'ledger.json'))
    native.restore_budget('personal', portable_snapshot(fixture_state(2)))
    session.bind(native)
    ledger = LedgerService(TrackedStorage(native, session), budget_id=session.id)
    checkpoints = []
    checkpoint = session.checkpoint
    def save():
        checkpoint()
        checkpoints.append(session.path.read_bytes())
    monkeypatch.setattr(session, 'checkpoint', save)
    identifiers = ['fictional-0', 'fictional-1']
    try:
        for index, action in enumerate(('void', 'unvoid'), 1):
            plan = ledger.query('preview_' + action + '_transactions', 'personal', parameters={'transactionIds': identifiers})
            payload = {'transactionIds': identifiers, 'fingerprint': plan['data']['fingerprint']}
            args = {'request_id': 'saved-' + action, 'expected_revision': plan['revision']}
            result = ledger.command(action + '_transactions', 'personal', payload, **args)
            assert ledger.command(action + '_transactions', 'personal', payload, **args) == result
            assert len(checkpoints) == index
            container = decode_container(checkpoints[-1])
            files = {name.removeprefix('plugins/' + kind + '/'): raw for name, raw in container.files.items()
                     if name.startswith('plugins/' + kind + '/')}
            restored = factory(tmp_path / (f'{action}.sqlite' if kind == 'sqlite' else f'{action}.json'))
            try:
                restored.restore_budget_files('personal', files)
                rows = restored.export_budget('personal')['transactions']
                assert len(rows) == 2
                assert all(row.get('voided', False) is (action == 'void') for row in rows)
                assert restored.export_budget('personal')['accounts'] == native.export_budget('personal')['accounts']
            finally:
                restored.close()
    finally:
        native.close()


def test_unsupported_authority_refuses_before_mutation():
    class Unsupported:
        def apply_transaction_void(self, *_):
            pytest.fail('Unsupported authority received a mutation')
    ledger = LedgerService(Unsupported())
    with pytest.raises(StoreError, match='Update the storage'):
        ledger.query('preview_void_transactions', 'personal', parameters={'transactionIds': ['fictional']})
    with pytest.raises(StoreError, match='Update the storage'):
        ledger.command('void_transactions', 'personal', {'transactionIds': ['fictional'], 'fingerprint': 'a' * 64},
                       request_id='fictional-intent', expected_revision=ledger.revision)


def test_older_history_authority_cannot_drop_explicit_unvoid_decision(store):
    account = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 100000})
    identifier = store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
        'merchant': 'Fictional shop', 'amount': 123, 'categoryId': None})
    for action in ('void', 'unvoid'):
        plan = store.preview_transaction_void('personal', [identifier], action)
        store.apply_transaction_void('personal', [identifier], action, plan['fingerprint'])
    store.edit_transaction('personal', identifier, note='Later note')
    target = store.budget_history('personal')['entries'][0]['id']
    plan = store.history_preview('personal', 'undo', target=target)
    assert all(change[side].get('voided') is False for change in plan['changes']
               if change['path'][0] == 'transactions' for side in ('before', 'after'))
    class OlderAuthority:
        def __getattr__(self, name):
            return getattr(store, name)
        def supports_transaction_voiding(self):
            return False
    ledger = LedgerService(OlderAuthority())
    before = store.export_budget('personal')
    with pytest.raises(StoreError, match='Update the storage backend'):
        ledger.command('history_apply', 'personal', {key: plan[key] for key in ('action', 'target', 'fingerprint')},
                       request_id='older-history', expected_revision=ledger.revision)
    assert store.export_budget('personal') == before


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_https_remote_authority_preview_apply_and_restore(kind, tmp_path):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from whab_plugin_server.plugin import plugin

    from whab.plugins.remote_transport import RemoteStore
    from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

    if kind == 'sqlite':
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    native = Store(tmp_path / ('fictional.sqlite' if kind == 'sqlite' else 'fictional.json'))
    native.restore_budget('personal', portable_snapshot(fixture_state(2)))
    authority = LedgerService(native, budget_id='fictional')
    app = FastAPI()
    token = 'fictional-void-token-' + 'a' * 32
    binding = BoundRemoteInterface(plugin.interface.adapter, SimpleNamespace(
        service=authority, config={'SERVER_TOKEN': token}, budget_id='fictional'))
    app.include_router(create_remote_router(lambda: binding))
    try:
        with TestClient(app, base_url='https://fictional.example') as client:
            def dispatch(request):
                response = client.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
                return httpx.Response(response.status_code, json=response.json())
            remote = RemoteStore('https://fictional.example', token, 'fictional', transport=httpx.MockTransport(dispatch))
            try:
                assert remote.supports_transaction_voiding()
                before = native.export_budget('personal')
                for action in ('void', 'unvoid'):
                    preview = remote.preview_transaction_void('personal', ['fictional-0', 'fictional-1'], action)
                    saved = remote.apply_transaction_void('personal', ['fictional-0', 'fictional-1'], action, preview['fingerprint'])
                    assert saved == preview
                    assert len(native.transactions('personal')) == (0 if action == 'void' else 2)
                assert native.export_budget('personal')['accounts'] == before['accounts']
                for bad in (None, [], True, 'delete'):
                    with pytest.raises(StoreError, match='Choose void or unvoid'):
                        remote.preview_transaction_void('personal', ['fictional-0'], bad)
                    with pytest.raises(StoreError, match='Choose void or unvoid'):
                        remote.apply_transaction_void('personal', ['fictional-0'], bad, 'a' * 64)
            finally:
                remote.close()
    finally:
        native.close()
