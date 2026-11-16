"""Dated transfer funding is explained without treating transfers as income."""

import re

from test_summary_explanations import disclosure, equation, overview_cells, terms


def test_cross_scope_transfer_funding_and_off_page_pair_review(client, store):
    cash = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 200000})
    archive = store.add_account('personal', {'name': 'Fictional archived savings', 'type': 'depository', 'balance': 0, 'onBudget': False})
    out = store.add_transaction('personal', {'accountId': cash, 'date': '2026-10-31', 'merchant': 'Fictional out', 'amount': 12345, 'categoryId': None})
    incoming = store.add_transaction('personal', {'accountId': archive, 'date': '2026-11-01', 'merchant': 'Fictional in', 'amount': -12345, 'categoryId': None})
    store.update_account('personal', archive, {'closed': True})
    pairs = [{'outId': out, 'inId': incoming}]
    preview = store.preview_transfer_matches('personal', pairs)
    store.apply_transfer_matches('personal', pairs, fingerprint=preview['fingerprint'])
    page = client.get('/budgets/home/budget/2026-10/usd').text
    funding = overview_cells(page, 'Transfer funding')[0][1]
    assert '<span data-private-amount>-123</span>' in funding
    explanation = disclosure(funding, 'Transfer funding', '2026-10')
    assert 'separate from income and spending' in explanation
    ready = disclosure(overview_cells(page, 'Available to assign')[0][1], 'Available to assign', '2026-10')
    assert ('Transfer funding', equation(-12345, '+', 'USD')) in terms(ready)
    register = client.get('/budgets/home/transactions/2026-10').text
    row = re.search(r'<tr class="transaction-row" data-transaction-id="'+re.escape(out)+r'".*?</tr>', register, re.S)[0]
    assert 'data-review-classified="true"' in row
    assert not re.search(r'data-transaction-reviewed[^>]* disabled', row)
    assert client.patch('/api/transactions/'+out, json={'workspace': 'personal', 'reviewed': True}).status_code == 200
