"""Native pairs cross the real worker, receipt, restart and saved-file boundary."""

from copy import deepcopy

import pytest

from whab.domain.accounts import normalize_account
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.plugins import LedgerService, RequestConflict, RevisionConflict, StorageContext
from whab.plugins.storage_proxy import StorageWorkerProxy
from whab.plugins.storage_worker import StorageWorker


def fixture_state(count=100):
    state = initial_state('personal', '2026-11')
    state['accounts'] = {name: normalize_account({
        'id': name, 'name': '旅行 ' + name, 'type': 'depository', 'balance': 100000,
        'currency': 'USD', 'source': 'manual', 'institution': '', 'stale': False,
        'balanceUnknown': False,
    }) for name in ('a', 'b')}
    state['transactions'] = {}
    for index in range(count):
        for suffix, account, sign in [('out', 'a', 1), ('in', 'b', -1)]:
            identifier = f'{suffix}-{index}'
            state['transactions'][identifier] = {
                'id': identifier, 'accountId': account, 'date': '2026-11-01',
                'merchant': '  Fictional 商店  ', 'merchantLogo': None,
                'amount': sign * (100 + index), 'categoryId': None, 'pending': False,
                'reviewed': False, 'source': 'manual', 'note': '[YNAB]  \nPreserved',
                'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': True,
                'deleted': False, 'categoryAgent': 'Earlier agent',
            }
    return state


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_hundred_pairs_one_commit_save_exact_restart_receipt_and_restored_budget(kind, tmp_path):
    context = StorageContext(tmp_path / 'fictional', 'sandbox', {})
    intents = tmp_path / 'intents'
    store = StorageWorkerProxy(StorageWorker(kind), context, intents)
    pairs = [{'outId': f'out-{index}', 'inId': f'in-{index}'} for index in range(100)]
    saves = []
    try:
        store.restore_budget('personal', portable_snapshot(fixture_state()))
        service = LedgerService(store, budget_id='fictional', on_commit=lambda: saves.append(store.export_budget_files()))
        untouched = deepcopy(store.export_budget('personal'))
        policy = service.query('transfer_policy', 'personal')['data']
        assert policy['maxPairs'] == 100 and policy['twoLegsRequired']
        preview = service.query('preview_transfer_matches', 'personal', parameters={'pairs': pairs, 'reviewed': True})
        assert store.export_budget('personal') == untouched and saves == []
        payload = {'pairs': pairs, 'reviewed': True, 'fingerprint': preview['data']['fingerprint']}
        args = {'request_id': 'fictional-bulk', 'expected_revision': preview['revision'],
                'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-pairs'}}
        generation = store.commit_evidence().generation
        first = service.command('match_transfers', 'personal', payload, **args)
        assert first['data'] == preview['data']
        assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
        assert service.command('match_transfers', 'personal', payload, **args) == first
        assert len(saves) == 1 and store.commit_evidence().generation == generation + 1
        with pytest.raises(RequestConflict):
            service.command('match_transfers', 'personal', {**payload, 'reviewed': False}, **args)
        with pytest.raises(RevisionConflict):
            service.command('match_transfers', 'personal', payload, **{**args, 'request_id': 'new-stale'})
        saved = store.export_budget('personal')
        assert all(row['reviewed'] and row['transferPeerId'] for row in saved['transactions'])
        assert saved['accounts'] == untouched['accounts']
        assert saved['assignments'] == untouched['assignments']
        assert len(first['data']['changes']) == 200
        assert all(row['merchant'] == '  Fictional 商店  ' and row['note'] == '[YNAB]  \nPreserved' for row in saved['transactions'])
        history = store.budget_history('personal')['entries'][0]
        assert history['actor'] == 'Codex' and history['batchId'] == 'fictional-pairs'
    finally:
        store.close()
    reopened = StorageWorkerProxy(StorageWorker(kind), context, intents)
    restored = StorageWorkerProxy(StorageWorker(kind), StorageContext(tmp_path / 'empty', 'sandbox', {}), tmp_path / 'empty-intents')
    try:
        service = LedgerService(reopened, budget_id='fictional', on_commit=lambda: saves.append(None))
        assert service.command('match_transfers', 'personal', payload, **args) == first
        assert len(saves) == 1 and reopened.commit_evidence().generation == generation + 1
        restored.restore_budget_files('personal', saves[0])
        assert restored.export_budget('personal')['transactions'] == saved['transactions']
        for action in ('undo', 'redo'):
            plan = service.query('history_preview', 'personal', parameters={'action': action, 'target': history['id']})
            service.command('history_apply', 'personal', {key: plan['data'][key] for key in ('action', 'target', 'fingerprint')}, request_id='fixture-' + action, expected_revision=plan['revision'])
        assert {row['id']: row for row in reopened.export_budget('personal')['transactions']} == {row['id']: row for row in saved['transactions']}
    finally:
        reopened.close()
        restored.close()


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_https_and_remote_authority_bulk_pairing_and_conflicts(kind, tmp_path):
    from types import SimpleNamespace

    import httpx
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from whab_plugin_server.plugin import plugin

    from whab.plugins.remote_transport import RemoteStore
    from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router
    from whab.web.routes.ledger import create_router

    if kind == 'sqlite':
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    native = Store(tmp_path / ('fictional.sqlite' if kind == 'sqlite' else 'fictional.json'))
    native.restore_budget('personal', portable_snapshot(fixture_state(4)))
    authority = LedgerService(native, budget_id='fictional')
    app = FastAPI()
    app.include_router(create_router(authority))
    token = 'fictional-transfer-token-' + 'a' * 32
    binding = BoundRemoteInterface(plugin.interface.adapter, SimpleNamespace(service=authority, config={'SERVER_TOKEN': token}, budget_id='fictional'))
    app.include_router(create_remote_router(lambda: binding))
    pairs = [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}]
    try:
        with TestClient(app, base_url='https://fictional.example') as client:
            def dispatch(request):
                response = client.request(request.method, str(request.url), headers=dict(request.headers), content=request.content)
                return httpx.Response(response.status_code, json=response.json())
            remote = RemoteStore('https://fictional.example', token, 'fictional', transport=httpx.MockTransport(dispatch))
            try:
                assert remote.supports_transfer_integrity() and remote.supports_bulk_transfer_matching()
                preview = remote.preview_transfer_matches('personal', pairs, True)
                before = native.export_budget('personal')
                assert len(remote.transfer_audit('personal', {})['issues']) == 0
                assert native.export_budget('personal') == before
                result = remote.apply_transfer_matches('personal', pairs, True, preview['fingerprint'])
                assert result == preview
                assert all(row['reviewed'] for row in native.transactions('personal') if row['id'] in {'out-0', 'in-0', 'out-1', 'in-1'})
            finally:
                remote.close()
            # The local HTTPS adapter accepts the same exact atomic envelope.
            pairs = [{'outId': 'out-2', 'inId': 'in-2'}, {'outId': 'out-3', 'inId': 'in-3'}]
            body = {'workspace': 'personal', 'operation': 'preview_transfer_matches', 'parameters': {'pairs': pairs}}
            preview = client.post('/api/transfers/query', json=body).json()
            apply = {'workspace': 'personal', 'operation': 'match_transfers', 'parameters': {'pairs': pairs, 'fingerprint': preview['data']['fingerprint']}, 'revision': preview['revision'], 'requestId': 'local-bulk', 'history': {'actor': 'Codex', 'actorKind': 'agent'}}
            first = client.post('/api/transfers/bulk', json=apply)
            assert first.status_code == 200
            assert client.post('/api/transfers/bulk', json=apply).json() == first.json()
            stale = client.post('/api/transfers/bulk', json={**apply, 'requestId': 'stale-bulk'})
            assert stale.status_code == 409 and stale.json()['code'] == 'transfer_revision_conflict'
            conflict = client.post('/api/transfers/bulk', json={**apply, 'parameters': {**apply['parameters'], 'reviewed': True}})
            assert conflict.status_code == 409 and conflict.json()['code'] == 'transfer_request_conflict'
    finally:
        native.close()


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_portable_session_pair_and_repair_each_checkpoint_once(kind, tmp_path, monkeypatch):
    from whab_plugin_json import Store as JSONStore
    from whab_plugin_sqlite import Store as SQLiteStore

    from whab.budgets.container import decode_container
    from whab.budgets.session import BudgetSession, TrackedStorage

    factory = SQLiteStore if kind == 'sqlite' else JSONStore
    session = BudgetSession.create(tmp_path / 'runtime', 'sandbox', 'Fictional transfers', storage=kind, path=tmp_path / 'fictional.whab')
    native = factory(session.storage_home / ('ledger.sqlite' if kind == 'sqlite' else 'ledger.json'))
    state = fixture_state(2)
    state['transactions']['legacy-zero'] = {**state['transactions']['out-0'], 'id': 'legacy-zero', 'amount': 0, 'isTransfer': True, 'inferredTransfer': True, 'reviewed': True}
    native.restore_budget('personal', portable_snapshot(state))
    session.bind(native)
    tracked = TrackedStorage(native, session)
    ledger = LedgerService(tracked, budget_id=session.id)
    checkpoints = []
    original = session.checkpoint
    def checkpoint():
        original()
        checkpoints.append(session.path.read_bytes())
    monkeypatch.setattr(session, 'checkpoint', checkpoint)
    try:
        pairs = [{'outId': 'out-0', 'inId': 'in-0'}, {'outId': 'out-1', 'inId': 'in-1'}]
        preview = ledger.query('preview_transfer_matches', 'personal', parameters={'pairs': pairs})
        payload = {'pairs': pairs, 'fingerprint': preview['data']['fingerprint']}
        args = {'request_id': 'portable-match', 'expected_revision': preview['revision']}
        result = ledger.command('match_transfers', 'personal', payload, **args)
        assert len(checkpoints) == 1
        assert ledger.command('match_transfers', 'personal', payload, **args) == result
        assert len(checkpoints) == 1
        repair = ledger.query('preview_transfer_repair', 'personal', parameters={'transactionIds': ['legacy-zero']})
        ledger.command('repair_transfers', 'personal', {'transactionIds': ['legacy-zero'], 'fingerprint': repair['data']['fingerprint']}, request_id='portable-repair', expected_revision=repair['revision'])
        assert len(checkpoints) == 2 and checkpoints[0] != checkpoints[1]
        container = decode_container(checkpoints[-1])
        files = {name.removeprefix('plugins/'+kind+'/'): data for name, data in container.files.items() if name.startswith('plugins/'+kind+'/')}
        restored = factory(tmp_path / ('empty.sqlite' if kind == 'sqlite' else 'empty.json'))
        try:
            restored.restore_budget_files('personal', files)
            rows = {row['id']: row for row in restored.export_budget('personal')['transactions']}
            assert rows['out-0']['transferPeerId'] == 'in-0' and rows['out-1']['transferPeerId'] == 'in-1'
            assert not rows['legacy-zero']['reviewed'] and rows['legacy-zero']['inferredTransfer']
            assert rows['legacy-zero']['amount'] == 0
        finally:
            restored.close()
    finally:
        native.close()
