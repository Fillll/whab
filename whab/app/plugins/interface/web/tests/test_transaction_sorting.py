"""Header cycles, calendar links and full category paths use fictional records."""

import re
from html import unescape
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.datastructures import URL, QueryParams
from whab_plugin_web.views import transaction_month_links, transaction_window


def request(query='', month='2026-11'):
    url = URL(f'https://localhost:5174/budgets/fictional/checking/transactions/{month}/usd?{query}')
    return SimpleNamespace(url=url, query_params=QueryParams(url.query))


@pytest.mark.parametrize('field', ['date', 'account', 'merchant', 'category', 'note', 'amount', 'currency', 'reviewed'])
def test_heading_cycles_and_combined_sort_priorities(field):
    query = 'q=東京&review=reviewed&accounts=a&accounts=b&tx_page=2'
    for direction, following in [('none', 'asc'), ('asc', 'desc'), ('desc', None)]:
        suffix = '' if direction == 'none' else f'&sort={field}:{direction}'
        state = transaction_window([], request(query + suffix))
        heading = state['sorts'][field]
        assert heading['direction'] == direction
        params = parse_qs(urlsplit(heading['url']).query)
        assert params.get('sort') == ([f'{field}:{following}'] if following else None)
        assert params['q'] == ['東京'] and params['accounts'] == ['a', 'b']
        assert 'tx_page' not in params
    state = transaction_window([], request('sort=date:desc&sort=amount:asc'))
    assert state['sort_count'] == 2 and state['sorts']['amount']['priority'] == 2
    assert parse_qs(urlsplit(state['sorts']['category']['url']).query)['sort'] == ['date:desc', 'amount:asc', 'category:asc']
    assert parse_qs(urlsplit(state['sorts']['date']['url']).query)['sort'] == ['amount:asc']


@pytest.mark.parametrize('month,previous,following', [('2026-01', '2025-12', '2026-02'), ('1900-01', None, '1900-02'), ('2200-12', '2200-11', None)])
def test_month_navigation_clears_old_ranges_and_retains_other_filters(month, previous, following):
    links = transaction_month_links(request('period=custom&start_date=2026-01-01&end_date=2026-12-31&all_dates=true&q=shop&review=reviewed&sort=date:asc&sort=amount:desc&tx_page=3', month), month)
    for key, expected in [('previous', previous), ('next', following), ('current', 'current-month')]:
        if expected is None:
            assert links[key] is None
            continue
        url = urlsplit(links[key])
        assert url.path.endswith(f'/transactions/{expected}/usd')
        query = parse_qs(url.query)
        assert query.get('period') == (['current-month'] if key == 'current' else None)
        assert not {'start_date', 'end_date', 'all_dates', 'tx_page'} & query.keys()
        assert query['sort'] == ['date:asc', 'amount:desc'] and query['q'] == ['shop']


def test_sorted_rendering_labels_and_canonical_address(client, store):
    account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
    category = store.add_category('personal', {'name': 'Public Transport', 'group': 'Everyday', 'target': 0})
    store.add_transaction('personal', {'accountId': account, 'merchant': 'Fictional train', 'date': '2026-11-01', 'amount': 100, 'categoryId': category})
    response = client.get('/budgets/home/transactions/2026-11/usd?sort=date:asc&sort=amount:desc')
    assert response.status_code == 200
    text = unescape(response.text)
    assert 'Everyday > Public Transport' in text
    assert 'priority 1' in text and 'priority 2' in text
    assert 'transaction-sort-indicator' in text and '<small>1</small>' in text
    assert 'data-transaction-month' in text
    assert re.findall(r'data-resize-column="([^"]+)"', text) == ['account', 'merchant', 'category', 'note']
    assert 'Resize Payee column' in text and 'aria-describedby="transaction-resize-help"' in text
    assert 'sort=date%3Aasc&sort=amount%3Adesc' in text or 'sort=date:asc&sort=amount:desc' in text
    assert re.search(r'href="[^"]*sort=amount%3Adesc[^"]*"', text)
    assert client.get('/budgets/home/transactions/2026-11/usd?sort=date:sideways').status_code == 400


def test_actual_visual_worker_retains_native_origin_and_paging(store, tmp_path):
    from fastapi.testclient import TestClient

    from whab.app import create_app
    from whab.config import Settings
    from whab.plugins import load_plugins

    account = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 10000})
    for index in range(101):
        store.add_transaction('personal', {'accountId': account, 'merchant': f'Fictional item {index:03}',
                                           'date': f'2026-11-{1 + index % 2:02}', 'amount': 100, 'categoryId': None})
    settings = Settings(tmp_path, 5551, 'sandbox', '', '', '', False, '', '', (), ui='web')
    app = create_app(settings=settings, store=store, plugins=load_plugins(('web',)))
    with TestClient(app, base_url='https://localhost:5551') as browser:
        response = browser.get('/budgets/home/transactions/2026-11/usd?sort=merchant:asc&sort=amount:desc')
        assert response.status_code == 200
        text = unescape(response.text)
        assert re.findall(r'data-merchant="([^"]+)"', text) == [f'Fictional item {index:03}' for index in range(100)]
        ordered = browser.get('/budgets/home/transactions/2026-11/usd?sort=date:asc&sort=merchant:desc')
        dates = re.findall(r'data-transaction-detail="date" value="([^"]+)"', ordered.text)
        assert len(dates) == 100 and dates == sorted(dates)
        for href in re.findall(r'<a[^>]*(?:data-transaction-sort="[^"]+"|data-transaction-month)[^>]*href="([^"]+)"', text):
            assert href.startswith('/budgets/'), href
        next_page = re.search(r'data-stream-next="([^"]+)"', text)[1]
        assert next_page.startswith('/budgets/')
        assert parse_qs(urlsplit(next_page).query)['sort'] == ['merchant:asc', 'amount:desc']
        following = browser.get(next_page, headers={'X-WHAB-Transactions-Fragment': '1'})
        assert following.status_code == 200
        assert 'Fictional item 100' in following.text
        assert following.text.count('class="transaction-row"') == 1
