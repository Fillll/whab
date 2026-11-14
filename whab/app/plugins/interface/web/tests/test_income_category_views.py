"""Web picker, budget and Insights use explicit core Income treatment."""

import re


def test_income_categories_are_pickable_but_not_expense_envelopes(client, store):
    account = store.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
    response = client.post('/api/categories', json={'workspace': 'personal', 'name': 'Salary', 'group': 'Finance', 'target': 0, 'kind': 'income'})
    assert response.status_code == 200
    category = next(row['id'] for row in store.categories('personal') if row['name'] == 'Salary')
    store.add_category('personal', {'name': 'Bonus', 'group': 'Income only', 'target': 0, 'kind': 'income'})
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
    income_details = client.get('/budgets/home/budget/2026-10/usd', params={'category': category})
    assert income_details.status_code == 200 and 'inspector-assignment:' + category not in income_details.text
    insights = client.get('/budgets/home/insights/2026-10/usd').text
    assert 'Explicit Income $5,000.00' in insights
    # Clearing the classification also clears review.
    response = client.patch(f'/api/transactions/{transaction}', json={'workspace': 'personal', 'categoryId': None})
    assert response.status_code == 200
    assert next(row for row in store.transactions('personal') if row['id'] == transaction)['reviewed'] is False


def test_classification_required_controls_and_explicit_transfer(client, store):
    account = store.add_account('personal', {'name':'Fictional review cash','type':'depository','balance':0})
    transaction = store.add_transaction('personal', {'accountId':account,'date':'2026-10-01',
        'merchant':'  Fictional transfer  ','amount':100})
    def markup():
        page = client.get('/budgets/home/transactions/2026-10?review=all').text
        return re.search(r'<tr class="transaction-row" data-transaction-id="' + transaction + r'".*?</tr>', page, re.S)[0]
    row=markup()
    assert 'data-review-classified="false"' in row
    assert re.search(r'data-transaction-reviewed[^>]* disabled',row)
    assert 'Choose an Expense, Income or Transfer category' in row
    response=client.post('/api/categories',json={'workspace':'personal','name':'Move money','group':'Technical','target':0,'kind':'transfer'})
    assert response.status_code==200
    category=next(row['id'] for row in store.categories('personal') if row['name']=='Move money')
    assert client.patch(f'/api/transactions/{transaction}',json={'workspace':'personal','categoryId':category}).status_code==200
    row=markup()
    assert 'Technical &gt; Move money (Transfer)' in row and 'data-category-kind="transfer"' in row
    assert 'data-review-classified="true"' in row
    assert not re.search(r'data-transaction-reviewed[^>]* disabled',row)
    assert client.patch(f'/api/transactions/{transaction}',json={'workspace':'personal','reviewed':True}).status_code==200
    assert client.patch(f'/api/transactions/{transaction}',json={'workspace':'personal','categoryId':None}).status_code==200
    saved=next(row for row in store.transactions('personal') if row['id']==transaction)
    assert saved['reviewed'] is False and saved['merchant']=='Fictional transfer'
    assert 'data-review-classified="false"' in markup()
