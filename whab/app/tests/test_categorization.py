"""The host protects review state and budget totals around optional ML suggestions."""

from copy import deepcopy
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from whab_plugin_random_forest_categorization.plugin import plugin
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.budgets.session import BudgetSession
from whab.config import Settings
from whab.domain.categorization import candidates, fingerprint
from whab.domain.errors import StoreError
from whab.plugins import Categorization, Plugin, PluginRegistry
from whab.services.categorization import CategorizationService, local_context
from whab.services.ledger import LedgerService

PLUGIN = plugin.id


@pytest.fixture
def history(store):
    account = store.add_account('personal', {'name': 'Test account', 'type': 'depository', 'balance': 0})
    one = store.add_category('personal', {'name': 'Groceries', 'target': 0, 'group': 'Spending'})
    two = store.add_category('personal', {'name': 'Dining', 'target': 0, 'group': 'Spending'})
    ids = []
    for i in range(60):
        identifier = store.add_transaction('personal', {'accountId': account, 'merchant': 'Market' if i % 2 else 'Cafe',
            'amount': 2000 if i % 2 else 999, 'date': str(date(2026, 1, 1) + timedelta(days=i // 2)),
            'categoryId': one if i % 2 else two})
        store.edit_transaction('personal', identifier, reviewed=True)
        ids.append(identifier)
    future = store.add_transaction('personal', {'accountId': account, 'merchant': 'Market', 'amount': 2000,
                                                'date': '2026-10-01', 'categoryId': None})
    return account, one, two, ids, future


@pytest.fixture
def client(store, tmp_path):
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', ())
    app = create_app(store=store, settings=settings, plugins=PluginRegistry((web_plugin, plugin)))
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        yield client


def action(client, action, **values):
    result = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {'action': action, 'pluginId': PLUGIN, **values}})
    assert result.status_code == 200, result.text
    return result.json()


def train(client, threshold=.5):
    result = action(client, 'train', name='Synthetic model', parameters={'trees': 20, 'leaf': 1, 'vocabulary': 50, 'threshold': threshold})
    session = result['session']
    while not result.get('complete'):
        result = action(client, 'step', session=session)
    action(client, 'select', modelId=result['model']['id'])
    return result['model']


def test_train_suggest_accept_atomic_and_visible(client, store, history):
    *_, future = history
    assert action(client, 'status')['eligible'] == 60
    before = deepcopy(store.workspace('personal', '2026-10'))
    model = train(client)
    assert model['partitions']['train']['count'] == 36
    assert model['test']['count'] == 12
    assert action(client, 'suggest')['suggested'] == 1
    assert store.workspace('personal', '2026-10') == before
    suggestions = client.get('/api/categorization/suggestions?workspace=personal').json()
    suggestion = suggestions[future]
    assert suggestion['categoryId'] == history[1]
    page = client.get('/budgets/home/transactions/2026-10/usd').text
    assert 'ML suggested' in page and f'data-suggestion-id="{suggestion["id"]}"' in page
    assert f'value="{history[1]}" selected' in page
    denied = client.patch(f'/api/transactions/{future}', json={'workspace': 'personal', 'suggestionId': suggestion['id'], 'reviewed': False})
    assert denied.status_code == 400
    result = client.patch(f'/api/transactions/{future}', json={'workspace': 'personal', 'suggestionId': suggestion['id'], 'reviewed': True})
    assert result.status_code == 200, result.text
    transaction = next(row for row in store.transactions('personal') if row['id'] == future)
    assert transaction['reviewed'] and transaction['categoryId'] == history[1]
    assert client.get('/api/categorization/suggestions?workspace=personal').json() == {}
    assert action(client, 'status')['eligible'] == 61
    assert action(client, 'status', **{})['models'][0]['test']['count'] == 12
    action(client, 'delete', modelId=model['id'])
    assert action(client, 'status')['active'] is None


def test_correction_dismissal_and_stale_suggestion(client, store, history):
    future = history[-1]
    train(client)
    action(client, 'suggest')
    service = client.app.state.ledger_service
    suggestion = service.category_suggestions('personal')[future]
    action(client, 'dismiss', transactionId=future, suggestionId=suggestion['id'])
    assert action(client, 'suggest')['suggested'] == 0
    state = service.categorization.state(PLUGIN, 'personal')
    state[future]['dismissed'] = False
    service.categorization._save(PLUGIN, 'personal', state)
    denied = client.patch(f'/api/transactions/{future}', json={'workspace': 'personal', 'suggestionId': str(uuid4()), 'reviewed': True})
    assert denied.status_code == 400
    service.edit_transaction('personal', future, category_id=history[2], reviewed=True, suggestion_id=suggestion['id'])
    assert next(row for row in store.transactions('personal') if row['id'] == future)['categoryId'] == history[2]


def test_cancel_expiry_training_failures_and_invalid_inputs(client, history, monkeypatch):
    run = action(client, 'train')
    busy = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {'action': 'train', 'pluginId': PLUGIN}})
    assert busy.status_code == 400
    action(client, 'cancel', session=run['session'])
    expired = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {'action': 'step', 'pluginId': PLUGIN, 'session': run['session']}})
    assert expired.status_code == 400
    assert action(client, 'status')['models'] == []
    run = action(client, 'train')
    service = client.app.state.ledger_service.categorization
    service.runs[run['session']]['expires'] = 0
    run2 = action(client, 'train')
    action(client, 'cancel', session=run2['session'])
    service.after_import()
    assert service.error is None
    active = client.app.state.budget_runtime._active.services['categorization']
    monkeypatch.setattr(active, 'predict', lambda *_: (_ for _ in ()).throw(RuntimeError('private data')))
    service.after_import()
    assert 'private data' not in service.error
    for payload in ({'action': 'unknown', 'pluginId': PLUGIN}, {'action': 'train', 'pluginId': PLUGIN, 'parameters': []}, {'action': 'train', 'pluginId': PLUGIN, 'unexpected': 1}):
        assert client.post('/api/categorization', json={'workspace': 'personal', 'payload': payload}).status_code == 400


def test_reviewed_selector_and_fingerprint(history, store):
    accounts = store.accounts('personal')
    categories = store.categories('personal')
    transactions = store.transactions('personal')
    valid = next(row for row in transactions if row['reviewed'])
    variants = [{**valid, 'deleted': True}, {**valid, 'pending': True}, {**valid, 'isTransfer': True},
                {**valid, 'inferredTransfer': True}, {**valid, 'splits': [{}]}, {**valid, 'categoryId': None},
                {**valid, 'accountId': 'missing'}, {**valid, 'reviewed': False}]
    assert candidates(variants, accounts, categories, reviewed=True) == []
    assert len(candidates(transactions, accounts, categories, reviewed=True)) == 60
    row = candidates([valid], accounts, categories, reviewed=True)[0]
    assert fingerprint(row) != fingerprint({**row, 'amount': row['amount'] + 1})
    assert candidates([{**valid, 'reviewed': False}], accounts, categories, reviewed=False) == []
    mismatch = [{**category, 'currency': 'EUR'} for category in categories]
    assert candidates([valid], accounts, mismatch, reviewed=True) == []


def test_portable_saved_models_and_disabled_plugin(store, history, tmp_path):
    session = BudgetSession.create(tmp_path, 'sandbox', 'ML budget', (PLUGIN,))
    session.bind(store)
    ledger = LedgerService(store, budget_id=session.id)
    category_service = CategorizationService(ledger, (plugin,), session.context)
    ledger.categorization = category_service
    run = category_service.action('personal', {'action': 'train', 'pluginId': PLUGIN, 'parameters': {'trees': 10, 'threshold': .5}})
    result = {}
    while not result.get('complete'):
        result = category_service.action('personal', {'action': 'step', 'pluginId': PLUGIN, 'session': run['session']})
    model = result['model']['id']
    category_service.action('personal', {'action': 'select', 'pluginId': PLUGIN, 'modelId': model})
    category_service.predict('personal')
    from whab.budgets.container import decode_container
    exported = decode_container(session.bundle())
    assert f'plugins/{PLUGIN}/models/{model}.json' in exported.files
    disabled = CategorizationService(ledger, (), session.context, (PLUGIN,))
    assert history[-1] in disabled.suggestions('personal')
    ledger.categorization = disabled
    suggestion = disabled.suggestions('personal')[history[-1]]
    ledger.edit_transaction('personal', history[-1], reviewed=True, suggestion_id=suggestion['id'])
    assert not disabled.suggestions('personal')


def test_local_paths_and_multiple_engines(store, tmp_path):
    context = local_context(tmp_path, 'budget', PLUGIN)
    assert context.read_file('missing') is None
    context.write_file('models/a.json', b'{}')
    assert context.read_file('models/a.json') == b'{}'
    context.delete_file('models/a.json')
    with pytest.raises(StoreError):
        context.read_file('../secret')
    ledger = LedgerService(store, budget_id='budget')
    with pytest.raises(StoreError, match='only one'):
        CategorizationService(ledger, (plugin, Plugin(id='second', display_name='Second', categorization=Categorization(lambda _: SimpleNamespace()))),
                              lambda key: local_context(tmp_path, 'budget', key))


def test_remote_host_trains_and_accepts_suggestions_once(store, history, tmp_path):
    import httpx
    from fastapi import FastAPI
    from whab_plugin_client.plugin import RemoteStore
    from whab_plugin_server.plugin import plugin as server_plugin

    from whab.plugins import InterfaceContext
    from whab.web.interface_adapter import BoundRemoteInterface, create_remote_router

    authority = LedgerService(store, budget_id='authority')
    authority.categorization = CategorizationService(authority, (plugin,), lambda key: local_context(tmp_path, 'authority', key))
    token = 'synthetic-server-token-' + 'x' * 32
    app = FastAPI()
    binding = BoundRemoteInterface(server_plugin.interface.adapter, InterfaceContext(authority, {'SERVER_TOKEN': token}, 'sandbox', 'authority'))
    app.include_router(create_remote_router(lambda: binding))
    with TestClient(app, base_url='https://budget.example') as server:
        def handle(request):
            response = server.request(request.method, str(request.url), content=request.content,
                                      headers=dict(request.headers))
            return httpx.Response(response.status_code, json=response.json())

        remote = RemoteStore('https://budget.example', token, 'authority', transport=httpx.MockTransport(handle))
        viewer = LedgerService(remote)
        run = viewer.categorize('personal', {'action': 'train', 'pluginId': PLUGIN, 'parameters': {'trees': 10, 'threshold': .5}})
        result = {}
        while not result.get('complete'):
            result = viewer.categorize('personal', {'action': 'step', 'pluginId': PLUGIN, 'session': run['session']})
        viewer.categorize('personal', {'action': 'select', 'pluginId': PLUGIN, 'modelId': result['model']['id']})
        viewer.categorize('personal', {'action': 'suggest', 'pluginId': PLUGIN})
        suggestion = viewer.category_suggestions('personal')[history[-1]]
        viewer.edit_transaction('personal', history[-1], reviewed=True, suggestion_id=suggestion['id'])
        assert next(row for row in store.transactions('personal') if row['id'] == history[-1])['reviewed']
        assert viewer.category_suggestions('personal') == {}
        remote.close()


def test_bank_ingestion_triggers_suggestions_without_assigning_provider_hint(client, store, history):
    from whab.storage.bank import BankStore

    train(client)
    tracked = client.app.state.tracked_store
    bank = BankStore(tracked, 'plaid', suggestions_only=True)
    bank.save_item('test-item', 'synthetic-ciphertext', 'Test bank')
    changes = {'added': [{'id': 'future-bank', 'accountId': 'bank-account', 'date': '2026-10-18', 'merchant': 'Market',
                         'amount': 2000, 'suggestedCategoryId': history[2], 'pending': False, 'source': 'plaid',
                         'inferredTransfer': False, 'pendingTransactionId': None}], 'modified': [], 'removed': []}
    accounts = [{'id': 'bank-account', 'name': 'Bank checking', 'institution': 'Test bank', 'type': 'depository',
                 'balance': 10000, 'currency': 'USD', 'source': 'plaid', 'itemId': 'test-item'}]
    bank.apply_sync('test-item', changes, 'cursor-1', accounts)
    row = next(row for row in store.transactions('personal') if row['id'] == 'future-bank')
    assert row['categoryId'] is None and row['reviewed'] is False
    suggestions = client.app.state.ledger_service.category_suggestions('personal')
    assert 'future-bank' in suggestions
    original = suggestions['future-bank']['id']
    bank.apply_sync('test-item', {'added': [], 'modified': changes['added'], 'removed': []}, 'cursor-2', accounts)
    assert client.app.state.ledger_service.category_suggestions('personal')['future-bank']['id'] == original


def test_demo_models_are_separate_from_personal_and_corrupt_suggestions_do_not_block_pages(client, history):
    trained = train(client)
    service = client.app.state.ledger_service.categorization
    assert service.demo_engines[PLUGIN].index() == {'active': None, 'models': []}
    assert service.engines[PLUGIN].index()['active'] == trained['id']
    service.contexts[PLUGIN].write_file('suggestions-personal.json', b'invalid')
    assert service.suggestions('personal') == {}
    assert client.get('/budgets/home/transactions/2026-10/usd').status_code == 200


def test_failed_training_clears_session_and_acceptance_provenance_failure_keeps_review(client, history, monkeypatch):
    run = action(client, 'train', parameters={'trees': -1})
    response = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {'action': 'step', 'pluginId': PLUGIN, 'session': run['session']}})
    assert response.status_code == 400
    service = client.app.state.ledger_service.categorization
    assert not service.runs
    trained = train(client)
    action(client, 'rename', modelId=trained['id'], name='Renamed model')
    assert action(client, 'status')['models'][0]['name'] == 'Renamed model'
    action(client, 'suggest')
    suggestion = service.suggestions('personal')[history[-1]]
    active = client.app.state.budget_runtime._active.services['categorization']
    monkeypatch.setattr(active, '_save', lambda *_: (_ for _ in ()).throw(OSError('private file path')))
    client.app.state.ledger_service.edit_transaction('personal', history[-1], reviewed=True, suggestion_id=suggestion['id'])
    assert service.error == 'Review saved. Its suggestion history could not be updated.'
    assert next(row for row in client.app.state.store.transactions('personal') if row['id'] == history[-1])['reviewed']


def test_enabling_ml_preserves_previously_imported_reviewed_category(client, store, history):
    from whab.storage.bank import BankStore

    tracked = client.app.state.tracked_store
    before = BankStore(tracked, 'plaid')
    before.save_item('old-item', 'synthetic-ciphertext', 'Example bank')
    transaction = {'id': 'already-categorized', 'accountId': 'old-bank', 'date': '2026-10-18', 'merchant': 'Market',
                   'amount': 2000, 'suggestedCategoryId': history[1], 'pending': False, 'source': 'plaid',
                   'inferredTransfer': False, 'pendingTransactionId': None}
    account = {'id': 'old-bank', 'name': 'Old checking', 'institution': 'Example bank', 'type': 'depository',
               'balance': 10000, 'currency': 'USD', 'source': 'plaid', 'itemId': 'old-item'}
    before.apply_sync('old-item', {'added': [transaction], 'modified': [], 'removed': []}, '1', [account])
    store.edit_transaction('personal', transaction['id'], reviewed=True)
    after = BankStore(tracked, 'plaid', suggestions_only=True)
    after.apply_sync('old-item', {'added': [], 'modified': [{**transaction, 'suggestedCategoryId': history[2]}], 'removed': []}, '2', [account])
    row = next(row for row in store.transactions('personal') if row['id'] == transaction['id'])
    assert row['categoryId'] == history[1] and row['reviewed'] is True


def test_register_suggestion_projection_does_not_read_entire_history(client, history, monkeypatch):
    train(client)
    action(client, 'suggest')
    ledger = client.app.state.ledger_service
    monkeypatch.setattr(ledger, 'transactions', lambda *_: (_ for _ in ()).throw(AssertionError('Full history read')))
    response = client.get('/budgets/home/transactions/2026-10/usd')
    assert response.status_code == 200 and 'ML suggested' in response.text


def test_save_failure_does_not_chain_private_model_and_allows_retry(client, history, monkeypatch):
    import traceback

    service = client.app.state.ledger_service.categorization
    engine = service.engines[PLUGIN]

    def completed(*_):
        yield {'stage': 'Ready to save', 'progress': 98}
        return {'vocabulary': ['private-model-marker']}

    def fail_save(*_):
        raise OSError('Cannot save model')

    monkeypatch.setattr(engine, 'train', completed)
    monkeypatch.setattr(engine, 'save', fail_save)
    run = action(client, 'train')
    action(client, 'step', session=run['session'])
    with pytest.raises(OSError) as failure:
        service.action('personal', {'action': 'step', 'pluginId': PLUGIN, 'session': run['session']})
    assert failure.value.__context__ is None
    assert 'private-model-marker' not in ''.join(traceback.format_exception(failure.value))
    assert not service.runs
    retry = action(client, 'train')
    action(client, 'cancel', session=retry['session'])


def test_model_size_failure_is_actionable_and_does_not_publish_model(client, history, monkeypatch):
    # The host exercises an actionable save rejection at its public engine
    # boundary. Plugin serialization limits have their own direct plugin tests;
    # changing a parent module cannot affect an installed child interpreter.
    engine = client.app.state.ledger_service.categorization.engines[PLUGIN]
    def too_large(*_):
        raise StoreError('Model is too large. Reduce trees, depth, or vocabulary.')
    monkeypatch.setattr(engine, 'save', too_large)
    run = action(client, 'train', parameters={'trees': 10})
    result = {}
    while result.get('progress', 0) < 98:
        result = action(client, 'step', session=run['session'])
    response = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {
        'action': 'step', 'pluginId': PLUGIN, 'session': run['session']}})
    assert response.status_code == 400
    assert response.json() == {'error': 'Model is too large. Reduce trees, depth, or vocabulary.'}
    assert action(client, 'status')['models'] == []
    assert not client.app.state.ledger_service.categorization.runs


def test_explicit_low_score_preview_preserves_cutoff_and_ledger(client, store, history, monkeypatch):
    model = train(client, threshold=.8)
    service = client.app.state.ledger_service.categorization
    engine = service.engines[PLUGIN]
    original = engine.context.read_file(f"models/{model['id']}.json")
    before = deepcopy(store.workspace('personal', '2026-10'))
    monkeypatch.setattr(engine, 'predict', lambda rows: [])
    monkeypatch.setattr(engine, 'preview', lambda rows: [
        {'transactionId': row['id'], 'categoryId': history[1], 'score': .6,
         'modelId': model['id'], 'belowCutoff': True} for row in rows])
    assert action(client, 'suggest')['suggested'] == 0
    assert action(client, 'status')['previewAvailable']
    assert action(client, 'preview') == {'suggested': 1, 'lowScore': 1}
    suggestion = service.suggestions('personal')[history[-1]]
    assert suggestion['belowCutoff'] is True and suggestion['score'] == .6
    page = client.get('/budgets/home/transactions/2026-10/usd').text
    assert '60% · Low score' in page and 'class="ml-suggestion"' in page
    assert '<svg width="13"' in page
    assert engine.context.read_file(f"models/{model['id']}.json") == original
    assert store.workspace('personal', '2026-10') == before
    action(client, 'dismiss', transactionId=history[-1], suggestionId=suggestion['id'])
    assert action(client, 'preview')['suggested'] == 0
    assert action(client, 'suggest')['suggested'] == 0
    monkeypatch.setattr(engine, 'preview', None)
    assert not action(client, 'status')['previewAvailable']
    response = client.post('/api/categorization', json={'workspace': 'personal', 'payload': {'action': 'preview', 'pluginId': PLUGIN}})
    assert response.status_code == 400 and 'does not support' in response.json()['error']



def test_local_context_isolates_budget_files_and_preserves_default_location(tmp_path):
    legacy = tmp_path / 'plugin-data' / PLUGIN / 'models.json'
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b'legacy-model-index')
    original = local_context(tmp_path, 'default', PLUGIN)
    first = local_context(tmp_path, 'first', PLUGIN)
    second = local_context(tmp_path, 'second', PLUGIN)
    assert original.read_file('models.json') == b'legacy-model-index'
    assert first.read_file('models.json') is second.read_file('models.json') is None
    first.write_file('models.json', b'first-model-index')
    assert second.read_file('models.json') is None
    second.delete_file('models.json')
    assert first.read_file('models.json') == b'first-model-index'
    assert original.read_file('models.json') == b'legacy-model-index'
    for budget_id, plugin_id in (('../other', PLUGIN), ('default', '../other')):
        with pytest.raises(StoreError, match='namespace'):
            local_context(tmp_path, budget_id, plugin_id)


def test_categorization_rejects_context_from_another_budget(store, tmp_path):
    ledger = LedgerService(store, budget_id='first')
    with pytest.raises(StoreError, match='another budget'):
        CategorizationService(ledger, (plugin,), lambda key: local_context(tmp_path, 'second', key))


def test_new_import_and_blank_budget_cannot_use_source_models_or_training_history(store, history, tmp_path):
    import hashlib

    from tests.web.test_file_import_routes import payload
    from whab.plugins import FileImport

    configuration = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', (PLUGIN,), ui='')
    importer = Plugin('dump', 'Synthetic import', file_import=FileImport(lambda _: payload()))
    app = create_app(store=store, settings=configuration, plugins=PluginRegistry((plugin, importer)))
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        model = train(client)
        run = action(client, 'train')
        raw = b'synthetic-budget-export'
        imported = client.post('/budgets/home/api/plugins/dump/import/new/commit', content=raw, headers={
            'Content-Type': 'application/zip', 'X-Import-Currency': 'USD',
            'X-Import-Operation': str(uuid4()), 'X-Import-Fingerprint': hashlib.sha256(raw).hexdigest(),
        })
        assert imported.status_code == 200, imported.text
        imported_id = imported.json()['id']
        selected = client.post(f'/budgets/home/api/budgets/{imported_id}/activate', json={})
        assert selected.status_code == 200
        assert client.get('/api/budget').json()['id'] == imported_id
        assert 'httponly' in selected.headers['set-cookie'].lower()
        assert 'secure' in selected.headers['set-cookie'].lower()
        blank = client.post('/api/budget/new', json={'name': 'Blank budget'}).json()['id']

        def scoped(key, action, **values):
            return client.post(f'/budgets/{key}/api/categorization', json={
                'workspace': 'personal', 'payload': {'action': action, 'pluginId': PLUGIN, **values}})

        # The cookie selects the blank budget; explicit addresses still own each request.
        for key, eligible in ((imported_id, 1), (blank, 0)):
            status = scoped(key, 'status').json()
            assert status['models'] == [] and status['active'] is None and status['eligible'] == eligible
            for operation in ('select', 'rename', 'delete'):
                assert scoped(key, operation, modelId=model['id'], name='Wrong budget').status_code == 400
            for operation in ('step', 'cancel'):
                assert scoped(key, operation, session=run['session']).status_code == 400
            own = scoped(key, 'train').json()
            assert scoped(key, 'step', session=own['session']).status_code == 400
            assert scoped(key, 'suggest').json()['suggested'] == 0
            assert scoped(key, 'preview').json()['suggested'] == 0
        source = scoped('home', 'status').json()
        assert source['eligible'] == 60 and source['active'] == model['id']
        assert source['models'][0]['id'] == model['id']
        assert scoped('home', 'cancel', session=run['session']).status_code == 200
