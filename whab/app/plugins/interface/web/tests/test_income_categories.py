"""Web picker, budget and Insights use explicit core Income treatment."""

import re


def test_income_categories_are_pickable_but_not_expense_envelopes(client, store):
    account = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
    response = client.post('/api/categories', json={'workspace': 'personal', 'name': 'Salary', 'group': 'Finance', 'target': 0, 'kind': 'income'})
    assert response.status_code == 200
    category = next(row['id'] for row in store.categories('personal') if row['name'] == 'Salary')
    fees = store.add_category('personal', {'name': 'Fees', 'group': 'Finance', 'target': 0})
    transaction = store.add_transaction('personal', {'accountId': account, 'date': '2026-10-01',
        'merchant': 'Fictional employer', 'amount': -500000, 'categoryId': category})
    store.edit_transaction('personal', transaction, reviewed=True)
    page = client.get('/budgets/home/transactions/2026-10?review=all').text
    assert 'Finance &gt; Salary (Income)' in page
    row = re.search(r'<tr class="transaction-row" data-transaction-id="' + transaction + r'".*?</tr>', page, re.S)[0]
    assert 'Reviewed: Fictional employer' in row
    budget = client.get('/budgets/home/budget/2026-10/usd').text
    assert f'data-budget-key="category:{category}"' not in budget
    assert f'data-budget-key="category:{fees}"' in budget
    assert 'legacy unclassified inflows' in budget
    insights = client.get('/budgets/home/insights/2026-10/usd').text
    assert 'Explicit Income $5,000.00' in insights
    # Explicit review stays independent when category is cleared.
    response = client.patch(f'/api/transactions/{transaction}', json={'workspace': 'personal', 'categoryId': None})
    assert response.status_code == 200
    assert next(row for row in store.transactions('personal') if row['id'] == transaction)['reviewed'] is True
