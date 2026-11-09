"""History lives in Settings and guarded HTTP actions share the ledger boundary."""


def test_settings_history_routes_preview_apply_and_conflict(client):
    page = client.get('/budgets/home/transactions/2026-10/usd').text
    assert 'data-budget-history' in page and 'budget-history-page' in page
    assert 'data-history-apply' in page and 'data-history-retry' in page
    account = client.post('/api/accounts', json={'workspace':'personal','name':'Fictional history','type':'depository','balance':100}).json()['accountId']
    history = client.get('/api/history', params={'workspace':'personal'}).json()
    target = history['data']['entries'][0]['id']
    preview = client.post('/api/history/preview', json={'workspace':'personal','action':'undo','target':target})
    assert preview.status_code == 200, preview.text
    observed = preview.json()
    body = {'workspace':'personal','action':'undo','target':target,'fingerprint':observed['data']['fingerprint'],'revision':observed['revision'],'requestId':'fictional-undo'}
    assert client.post('/api/history/apply', json=body).status_code == 200
    assert client.post('/api/history/apply', json=body).status_code == 200
    assert account not in {a['id'] for a in client.get('/api/workspace?workspace=personal&month=2026-10').json()['accounts']}
    assert client.get('/api/history?workspace=personal').json()['data']['total'] == 2
    assert client.post('/api/history/apply', json={**body,'requestId':'new-intent'}).status_code == 409
    assert client.get('/api/history?workspace=personal&limit=101').status_code == 400
    assert client.post('/api/history/preview', json={'workspace':'personal','action':'invalid'}).status_code == 400
    assert client.post('/api/history/preview', json={'workspace':'personal','action':'snapshot','unexpected':True}).status_code == 400
