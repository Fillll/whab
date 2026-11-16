"""Local HTTPS review endpoints never auto-match or auto-review."""

import re
from copy import deepcopy

import pytest
from fastapi.testclient import TestClient

from whab.app import create_app
from whab.config import Settings
from whab.domain.history import financial


@pytest.mark.parametrize('zero_pair', [False, True])
def test_web_proposal_preview_confirm_unmatch_and_stale_guards(store, tmp_path, zero_pair):
    from whab_plugin_sqlite import Store

    seed = Store(':memory:', _authority=False) if zero_pair else store
    ids = []
    for name, amount in [('旅行 🍏', 100), ('Сбережения', -100)]:
        account = seed.add_account('personal', {'name': name, 'type': 'depository', 'balance': 10000})
        ids.append(seed.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional', 'amount': amount, 'categoryId': None}))
    transfer = seed.add_category('personal', {'name': 'Fictional transfer', 'group': 'Technical', 'kind': 'transfer'})
    expense = seed.add_category('personal', {'name': 'Fictional expense', 'group': 'Everyday'})
    if zero_pair:
        snapshot = seed.export_budget('personal')
        seed.close()
        for row in snapshot['transactions']:
            if row['id'] in ids:
                row.update(amount=0, source='ynab', inferredTransfer=True)
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        store.restore_budget('personal', snapshot)
    settings = Settings(tmp_path, 5173, 'sandbox', '', '', '', False, '', '', (), ui='web')
    app = create_app(store=store, settings=settings)
    with TestClient(app, base_url='https://127.0.0.1:5173') as client:
        before = deepcopy(store.export_budget())
        body = {'workspace': 'personal', 'operation': 'transfer_candidates', 'parameters': {}}
        proposals = client.post('/api/transfers/query', json=body).json()
        assert proposals['data']['total'] == (0 if zero_pair else 1)
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
        assert 'Choose a Transfer category, or keep the matched pair without a category' in page
        assert 'Transfer (matched)' in page and 'data-transfer-pair-valid="true"' in page
        assert not re.search(r'data-transaction-field="categoryId"[^>]* disabled', page)
        assert re.search(r'data-transaction-detail="amount"[^>]* disabled', page)
        assert 'Unmatch this confirmed transfer before changing financial fields' in page
        for category in [transfer, None]:
            assert client.patch(f'/api/transactions/{ids[0]}', json={'workspace':'personal','categoryId':category}).status_code == 200
            row = next(row for row in store.transactions('personal') if row['id'] == ids[0])
            assert row['categoryId'] == category and row['transferPeerId'] == ids[1] and row['isTransfer']
        for forbidden in [{'categoryId': expense}, {'amount': 200}]:
            unchanged = deepcopy(store.export_budget())
            refused = client.patch(f'/api/transactions/{ids[0]}', json={'workspace':'personal', **forbidden})
            assert refused.status_code == 400
            assert 'Unmatch' in refused.json()['error']
            assert store.export_budget() == unchanged
        assert 'Discard local edits &amp; reload' in page
        assert 'Saved changes are kept' in page
        for patch in [{'reviewed':True}, {'note':'[YNAB]  \nFictional memo'}]:
            assert client.patch(f'/api/transactions/{ids[0]}',json={'workspace':'personal',**patch}).status_code == 200
        saved_rows = store.transactions('personal')
        assert all(row.get('transferPeerId') for row in saved_rows)
        assert next(row for row in saved_rows if row['id']==ids[0])['reviewed'] is True
        assert next(row for row in saved_rows if row['id']==ids[0])['note'] == '[YNAB]  \nFictional memo'
        for bad in [{**payload, 'extra': True}, {**payload, 'action': 'automatic'}, {**payload, 'workspace': 'foreign'}, {**payload, 'requestId': ''}]:
            assert client.post('/api/transfers/apply', json=bad).status_code == 400
        preview = client.post('/api/transfers/query', json={**body, 'operation': 'preview_transfer_match', 'parameters': {'transactionIds': ids, 'action': 'unmatch'}}).json()
        result = client.post('/api/transfers/apply', json={**payload, 'action': 'unmatch', 'requestId': 'unmatch', 'revision': preview['revision'], 'fingerprint': preview['data']['fingerprint']})
        assert result.status_code == 200
        assert all(not row.get('transferPeerId') and not row['reviewed'] for row in store.transactions('personal'))
