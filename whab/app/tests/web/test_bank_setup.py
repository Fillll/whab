"""Actual local Web routes share durable account setup and update policies."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store

from tests.storage.test_bank_boundaries import account, changes, transaction
from whab.app import create_app
from whab.config import Settings
from whab.plugins import load_plugins
from whab.services.bank_runtime import BankCoordinator


class FictionalProvider:
    config = {'environment': 'sandbox', 'configured': True, 'liveEnabled': True, 'missingConfig': []}
    def __init__(self): self.calls = []
    def call(self, command, **values):
        self.calls.append(command)
        if command == 'connection_token':
            return 'fictional-link-token'
        if command == 'exchange_token':
            return {'item_id': 'item', 'access_token': 'fictional-access-token'}
        if command == 'read_sync':
            return {'accounts': [account()], 'changes': changes([transaction('old', date='2026-09-01'), transaction('new', date='2026-10-01')]), 'cursor': 'next', 'historical_complete': True}
        raise AssertionError(command)
    def close(self): pass


@pytest.fixture
def bank_app(tmp_path):
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '11' * 32, False, '', '', ('plaid',), ui='web')
    store = Store(tmp_path / 'budget.sqlite', month='2026-10', environment='sandbox')
    target = store.add_account('personal', {'name': 'Fictional existing checking', 'type': 'depository', 'balance': 0})
    injected = {}
    app = create_app(settings=settings, store=store, plugins=load_plugins(('sqlite', 'web', 'plaid')), bank_services=injected)
    provider = FictionalProvider()
    coordinator = BankCoordinator(provider, app.state.tracked_store, app.state.bank_stores['plaid'] if 'plaid' in app.state.bank_stores else app.state.bank_lifecycle.bank_store('plaid'),
        budget_id=app.state.ledger_service.budget_id, environment='sandbox', provider_id='plaid', generation=1,
        home=tmp_path / 'flows', encryption_key='11' * 32, requires_account_setup=True,
        background_scope=app.state.budget_runtime.background,
        automatic_sync=lambda: app.state.bank_jobs.automatic('plaid'))
    injected['plaid'] = coordinator
    try:
        with TestClient(app, base_url='https://127.0.0.1:5173') as client:
            yield SimpleNamespace(client=client, app=app, store=store, target=target, provider=provider)
    finally:
        store.close()


def test_web_connection_waits_for_explicit_history_and_preserves_destination_after_save(bank_app):
    client = bank_app.client
    flow = client.post('/api/plaid/link-token', json={}).json()
    response = client.post('/api/plaid/exchange', json={'flowId': flow['flow_id'], 'publicToken': 'fictional-public-token', 'institutionName': 'Fictional bank'})
    assert response.status_code == 200
    assert bank_app.store.transactions('personal') == []
    assert 'read_sync' not in bank_app.provider.calls
    assert client.post('/api/plaid/sync', json={'itemId': 'item'}).status_code == 400
    preview = client.post('/api/plaid/sync-preview', json={'itemId': 'item'}).json()
    assert preview['accounts'][0]['id'] == 'account'
    assert bank_app.store.get_item('item')['cursor'] is None
    body = {'itemId': 'item', 'accounts': [{'bankAccountId': 'account', 'accountId': bank_app.target}]}
    assert client.put('/api/plaid/sync-settings', json=body).status_code == 400  # no inferred date
    body['accounts'][0]['since'] = '2026-10-01'
    response = client.put('/api/plaid/sync-settings', json=body)
    assert response.status_code == 200 and response.json()['configured']
    synced = client.post('/api/plaid/sync', json={'itemId': 'item'})
    assert synced.json() == {'ok': True, 'added': 1, 'modified': 0, 'removed': 0}
    assert bank_app.store.transactions('personal')[0]['accountId'] == bank_app.target
    item = client.get('/api/plaid/items').json()['items'][0]
    assert item['accountLinks'][0]['name'] == 'Fictional existing checking'
    assert item['accountLinks'][0]['since'] == '2026-10-01'
    page = client.get('/budgets/home/accounts/2026-10/usd')
    assert page.status_code == 200 and 'Choose where to sync' in page.text
    assert 'data-sync-setup="true"' in page.text and 'Fictional existing checking' in page.text
    assert 'fictional-access-token' not in page.text


def test_web_frequency_and_manual_checks_use_shared_due_policy(bank_app):
    client = bank_app.client
    for interval in (3600, 10800, 21600, None):
        response = client.put('/api/plaid/schedule', json={'interval_seconds': interval})
        assert response.status_code == 200
        assert client.get('/api/plaid/schedule').json()['jobs'][0]['interval_seconds'] == interval
    assert not bank_app.app.state.bank_jobs.automatic('plaid')
    for interval in (True, 7200, -1):
        assert client.put('/api/plaid/schedule', json={'interval_seconds': interval}).status_code == 400
    assert client.put('/api/plaid/schedule', json={'interval_seconds': None}, headers={'origin': 'https://foreign.example'}).status_code == 403
