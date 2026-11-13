"""Web-only compact navigation keeps accessible headings and environment notices."""

import re

import pytest
from fastapi.testclient import TestClient
from whab_plugin_web.plugin import plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


def test_sidebar_control_has_names_and_prepaint_browser_bootstrap(client):
    text = client.get('/budgets/demo/transactions/current-month').text
    assert 'id="whab-sidebar" aria-label="Workspace sidebar"' in text
    assert 'aria-controls="whab-sidebar" aria-expanded="true" aria-label="Collapse sidebar"' in text
    assert 'class="sidebar-header"' in text
    assert re.search(r'aria-label="Transactions" title="Transactions"', text)
    head = text.split('</head>')[0]
    assert head.index('/static/sidebar.js') < head.index('rel="stylesheet"')
    script = re.search(r'<script src="([^"]*/static/sidebar.js[^\"]*)"', head)[1]
    assert client.get(script.replace('&amp;', '&')).status_code == 200


def test_duplicate_transactions_heading_is_only_hidden_for_all_accounts(client, store):
    text = client.get('/budgets/home/transactions/current-month').text
    assert '<h1 id="transaction-title" class="sr-only" tabindex="-1">Transactions</h1>' in text
    account = store.add_account('personal', {'name': 'Fictional wallet', 'type': 'depository', 'balance': 0})
    text = client.get('/budgets/home/transactions/current-month', params={'accounts':account}).text
    assert '<h1 id="transaction-title" tabindex="-1">Fictional wallet</h1>' in text
    fragment = client.get('/budgets/home/transactions/current-month', headers={'X-WHAB-Transactions-Fragment':'1'}).text
    assert 'class="sr-only"' in fragment and '/static/sidebar.js' not in fragment


@pytest.mark.parametrize('environment', ['production', 'sandbox'])
def test_only_production_live_badge_is_removed(store, tmp_path, environment):
    settings = Settings(tmp_path,5173,environment,'','','',False,'','',())
    with TestClient(create_app(store=store, settings=settings, plugins=PluginRegistry((plugin,))), base_url='https://127.0.0.1:5173') as client:
        for page in ['budget','transactions','accounts','insights']:
            text = client.get(f'/budgets/home/{page}/current-month/usd').text
            assert 'Live data' not in text
            assert ('Sandbox mode' in text) == (environment=='sandbox')
            demo = client.get(f'/budgets/demo/{page}/current-month/usd').text
            assert 'Sample data' in demo and 'You’re exploring sample data' in demo
