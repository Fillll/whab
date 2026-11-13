"""Local HTTPS review endpoints never auto-match or auto-review."""

from copy import deepcopy

from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings


def test_web_proposal_preview_confirm_unmatch_and_stale_guards(store, tmp_path):
    ids = []
    for name, amount in [('旅行 🍏', 100), ('Сбережения', -100)]:
        account = store.add_account('personal', {'name': name, 'type': 'depository', 'balance': 10000})
        ids.append(store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional', 'amount': amount, 'categoryId': None}))
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', (), ui='web')
    app = create_app(store=store, settings=settings)
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        before = deepcopy(store.export_budget())
        body = {'workspace': 'personal', 'operation': 'transfer_candidates', 'parameters': {}}
        proposals = client.post('/api/transfers/query', json=body).json()
        assert proposals['data']['total'] == 1
        preview = client.post('/api/transfers/query', json={**body, 'operation': 'preview_transfer_match', 'parameters': {'transactionIds': ids, 'action': 'match'}}).json()
        assert store.export_budget() == before
        payload = {'workspace': 'personal', 'transactionIds': ids, 'action': 'match', 'fingerprint': preview['data']['fingerprint'], 'revision': preview['revision'], 'requestId': 'fictional-browser'}
        saved = client.post('/api/transfers/apply', json=payload)
        assert saved.status_code == 200 and saved.json()['data']['count'] == 2
        assert client.post('/api/transfers/apply', json=payload).json() == saved.json()
        stale = client.post('/api/transfers/apply', json={**payload, 'requestId': 'stale-new'})
        assert stale.status_code == 409 and stale.json()['code'] == 'transfer_revision_conflict'
        assert all(row.get('transferPeerId') and not row['reviewed'] for row in store.transactions('personal'))
        page = client.get('/budgets/home/transactions/2026-11').text
        assert 'Suggest transfers' in page and 'data-transfer-panel' in page and 'data-transfer-unmatch' in page
        assert 'Unmatch this confirmed transfer before changing its category' in page and 'Transfer (excluded)' in page
        for bad in [{**payload, 'extra': True}, {**payload, 'action': 'automatic'}, {**payload, 'workspace': 'foreign'}, {**payload, 'requestId': ''}]:
            assert client.post('/api/transfers/apply', json=bad).status_code == 400
        preview = client.post('/api/transfers/query', json={**body, 'operation': 'preview_transfer_match', 'parameters': {'transactionIds': ids, 'action': 'unmatch'}}).json()
        result = client.post('/api/transfers/apply', json={**payload, 'action': 'unmatch', 'requestId': 'unmatch', 'revision': preview['revision'], 'fingerprint': preview['data']['fingerprint']})
        assert result.status_code == 200
        assert all(not row.get('transferPeerId') and not row['reviewed'] for row in store.transactions('personal'))
