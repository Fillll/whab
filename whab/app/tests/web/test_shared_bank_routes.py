"""The local Web/CLI transport uses the same host-owned shared connection facade."""

from tests.web.test_bank_setup import bank_app as bank_app


def test_named_profile_link_list_and_detach_use_scoped_host_operations(bank_app):
    app, client, coordinator = bank_app.app, bank_app.client, bank_app.coordinator
    lifecycle = app.state.bank_lifecycle
    lifecycle.migrate_connections('plaid')
    coordinator.connections = lifecycle.connections
    coordinator.profiles = lifecycle.account_profiles('plaid', ('PLAID_CLIENT_ID',))
    coordinator.config['sharedConnections'] = True
    configurations = []
    def call_with_config(config, command, **values):
        configurations.append(config)
        return bank_app.provider.call(command, **values)
    bank_app.provider.call_with_config = call_with_config
    body = {'profileId': 'second-account', 'config': {'PLAID_CLIENT_ID': 'fictional-client', 'PLAID_SECRET': 'fictional-secret'}}
    response = client.put('/api/plaid/profiles', json=body)
    assert response.status_code == 200 and response.json() == {'profileId': 'second-account', 'saved': True}
    assert client.get('/api/plaid/profiles').json() == {'profiles': [{'profileId': 'second-account'}]}
    flow = client.post('/api/plaid/link-token', json={'profileId': 'second-account'}).json()
    response = client.post('/api/plaid/exchange', json={'flowId': flow['flow_id'], 'publicToken': 'fictional-public-token', 'institutionName': 'Fictional bank'})
    assert response.status_code == 200
    listing = client.get('/api/plaid/connections')
    assert listing.status_code == 200 and listing.json()['unavailableBudgets'] == []
    connection = listing.json()['connections'][0]
    identifier = connection['itemId']
    assert identifier.startswith('shared:') and connection['profileId'] == 'second-account'
    assert connection['ownerBudgetId'] == 'default' and connection['attached']
    assert bank_app.store.transactions('personal') == []
    assert all(config['PLAID_CLIENT_ID'] == 'fictional-client' for config in configurations)
    assert 'fictional-secret' not in listing.text and 'shared-bank-v1:' not in listing.text
    assert client.get('/api/plaid/items').json()['items'][0]['id'] == identifier
    assert client.delete('/api/plaid/attachments/' + identifier).json() == {'itemId': identifier, 'attached': False}
    assert client.get('/api/plaid/items').json()['items'] == []
    assert client.post('/api/plaid/attachments', json={'itemId': identifier}).json()['attached']
    assert 'remove_token' not in bank_app.provider.calls
    assert client.post('/api/plaid/attachments', json={}).status_code == 400
    assert client.put('/api/plaid/profiles', json=body, headers={'origin': 'https://foreign.example'}).status_code == 403
    assert client.put('/budgets/demo/api/plaid/profiles', json=body).status_code == 403
