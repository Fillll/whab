"""Opening funding is distinct in Web summaries and existing zero rows stay reviewable."""

import re

import pytest
from test_summary_explanations import disclosure, equation, overview_cells, terms
from whab_plugin_web.views import display_money, money_number

from whab.plugins import WorkspaceImport


@pytest.mark.parametrize(('cash_amount', 'balance'), [(-5000, 3800), (5000, -6200)])
def test_opening_category_picker_zero_review_and_signed_funding(client, store, cash_amount, balance):
    account = {'id': 'fictional-cash', 'name': 'Fictional cash', 'institution': 'Fixture',
               'type': 'depository', 'balance': balance, 'source': 'fixture',
               'stale': False, 'balanceUnknown': False}
    transactions = tuple({'id': identifier, 'accountId': account['id'], 'date': '2026-10-01',
        'merchant': '  Existing cash '+identifier+'  ', 'amount': amount, 'categoryId': None,
        'pending': False, 'reviewed': False, 'source': 'fixture', 'note': ' [YNAB: cleared]\nKeep  ',
        'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': False, 'deleted': False}
        for identifier, amount in [('cash', cash_amount), ('correction', 1200), ('zero', 0)])
    store.import_workspace('personal', 'fictional', 'opening-web', WorkspaceImport(
        accounts=(account,), categories=(), transactions=transactions, assignments=(), budget_start_month='2026-10'))
    before = {row['id']: row for row in store.export_budget('personal')['transactions']}
    response = client.post('/api/categories', json={'workspace': 'personal', 'name': 'Initial 🍏',
        'group': 'Finance', 'target': 0, 'kind': 'opening_balance'})
    assert response.status_code == 200, response.text
    category = next(row['id'] for row in store.categories('personal') if row['name'] == 'Initial 🍏')
    for identifier in before:
        assert client.patch('/api/transactions/' + identifier,
            json={'workspace': 'personal', 'categoryId': category}).status_code == 200
    page = client.get('/budgets/home/transactions/2026-10?review=all').text
    assert 'Finance ⟩ Initial 🍏 (Opening Balance)' in page
    assert '(Opening_Balance)' not in page
    assert '<option value="opening_balance">Opening Balance · existing cash or debt</option>' in page
    zero_row = re.search(r'<tr class="transaction-row" data-transaction-id="zero".*?</tr>', page, re.S)[0]
    assert 'data-category-kind="opening_balance"' in zero_row
    assert 'data-review-classified="true"' in zero_row
    assert not re.search(r'data-transaction-reviewed[^>]* disabled', zero_row)
    assert client.patch('/api/transactions/zero', json={'workspace': 'personal', 'reviewed': True}).status_code == 200
    saved = {row['id']: row for row in store.export_budget('personal')['transactions']}
    for identifier, original in before.items():
        assert saved[identifier] == {**original, 'categoryId': category, 'categoryEdited': True,
                                     'reviewed': identifier == 'zero'}
    budget = client.get('/budgets/home/budget/2026-10/usd').text
    assert f'data-budget-key="category:{category}"' not in budget
    opening = disclosure(overview_cells(budget, 'Opening funding')[0][1], 'Opening funding', '2026-10')
    assert terms(opening) == [('Opening funding', money_number(balance))]
    assert 'without adding the same opening funding twice' in opening
    ready = disclosure(overview_cells(budget, 'Available to assign')[0][1], 'Available to assign', '2026-10')
    assert ('Opening funding', equation(balance, '+', 'USD')) in terms(ready)
    assert ('Income', '+ 0') in terms(ready)
    assert ('Available to assign', equation(balance, '=', 'USD')) in terms(ready)
    following = disclosure(overview_cells(budget, 'Opening funding')[1][1], 'Opening funding', '2026-11')
    assert terms(following) == [('Opening funding', '0')]
    insights = client.get('/budgets/home/insights/2026-10/usd', params={'insight_report': 'overview'}).text
    assert f'Opening funding <span data-private-amount>{display_money(balance, "USD")}</span> · separate from income' in insights
    assert '<span class="summary-label">TRANSACTIONS</span><strong>0</strong>' in insights
    assert 'Existing cash correction' not in insights


def test_missing_opening_projection_is_not_invented(client, store, monkeypatch):
    workspace = store.workspace
    def older_workspace(*args, **kwargs):
        data = workspace(*args, **kwargs)
        for summary in (data['summary'], *data['summariesByCurrency'].values()):
            summary.pop('openingFunding', None)
        return data
    monkeypatch.setattr(store, 'workspace', older_workspace)
    monkeypatch.setattr(store, 'workspace_periods', None)
    page = client.get('/budgets/home/budget/2026-10/usd').text
    assert all(body.strip() == '<span data-private-amount>—</span>' for _, body in overview_cells(page, 'Opening funding'))
    assert 'Breakdown unavailable' in overview_cells(page, 'Available to assign')[0][1]
    insights = client.get('/budgets/home/insights/2026-10/usd', params={'insight_report': 'overview'}).text
    assert 'Opening funding <span data-private-amount>—</span> · separate from income' in insights
