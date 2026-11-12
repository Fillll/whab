"""Ordering covers complete fictional selections, independent of native paging."""

import pytest

from whab.domain.errors import StoreError
from whab.services.ledger import LedgerService
from whab.services.register_sort import normalize_sort, sort_transactions


def test_sort_pages_cover_all_matches_and_preserve_the_ledger(store):
    service = LedgerService(store)
    account = service.add_account('personal', {'name': '東京 checking', 'type': 'depository', 'balance': 100000})
    category = service.add_category('personal', {'name': 'Transport', 'group': 'Living', 'target': 0})
    identifiers = []
    for index in range(105):
        identifiers.append(service.add_transaction('personal', {
            'accountId': account, 'date': '2026-10-01', 'merchant': f'Shop {index:03}',
            'amount': index + 1, 'categoryId': category,
        }))
    before = store.state('personal')
    revision = service.revision
    pages = [service.transaction_page('personal', '2026-10', 'USD', sort=['merchant:asc'], page=page)
             for page in (1, 2)]
    rows = [row for page in pages for row in page['transactions']]
    assert [row['merchant'] for row in rows] == [f'Shop {index:03}' for index in range(105)]
    assert {row['id'] for row in rows} == set(identifiers)
    assert [page['transactionPage']['count'] for page in pages] == [105, 105]
    assert all(page['transactionPage']['revision'] == revision for page in pages)
    assert service.transaction_page('personal', '2026-10', 'USD', sort=['amount:asc'])['transactions'][0]['amount'] == 105
    assert service.transaction_page('personal', '2026-10', 'USD', sort=['merchant:desc'], search='Shop 10', page_size=3)['transactionPage']['count'] == 5
    assert store.state('personal') == before and service.revision == revision


@pytest.mark.parametrize('ordering', [None, 'date:asc', ['date:up'], ['unknown:asc'], ['date:asc', 'date:desc'], [1], ['date:asc'] * 9])
def test_invalid_sorting_is_rejected(ordering):
    with pytest.raises(StoreError):
        normalize_sort(ordering)


def test_sort_keys_use_labels_unicode_casefold_signed_amounts_and_stable_ties():
    accounts = [{'id': 'a', 'name': '東京', 'currency': 'USD'}, {'id': 'b', 'name': 'Alpha', 'currency': 'EUR'}]
    categories = [{'id': 'c', 'name': 'Train', 'group': 'Travel'}]
    rows = [dict(id=str(index), accountId=account, categoryId=category, date='2026-10-01', merchant=merchant,
                 note=note, amount=amount, reviewed=reviewed)
            for index, (account, category, merchant, note, amount, reviewed) in enumerate([
                ('a', 'c', 'Straße', 'z', 50, False), ('b', None, 'STRASSE', 'a', -10, True),
                ('a', 'c', 'Other', 'a', 50, True)])]
    expected = {'date': ['2', '1', '0'], 'account': ['1', '2', '0'], 'category': ['2', '0', '1'],
                'merchant': ['2', '1', '0'], 'note': ['2', '1', '0'], 'amount': ['2', '0', '1'],
                'reviewed': ['0', '2', '1'], 'currency': ['1', '2', '0']}
    for field, identifiers in expected.items():
        assert [row['id'] for row in sort_transactions(rows, accounts, categories, [(field, 'asc')])] == identifiers
    assert [row['id'] for row in sort_transactions(rows, accounts, categories, [('account', 'desc'), ('reviewed', 'asc')])] == ['0', '2', '1']
