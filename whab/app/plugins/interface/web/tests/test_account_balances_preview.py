"""Acceptance previews use shipped rendering and independently specified money."""

import json
import re
import runpy
from pathlib import Path


def test_fictional_account_preview_has_real_signed_rows_and_read_only_controls(tmp_path):
    fixture = Path(__file__).parent / 'fixtures/build_account_balances_preview.py'
    builder = runpy.run_path(str(fixture))
    expected = builder['build'](tmp_path)
    assert json.loads((tmp_path / 'expected-values.json').read_text()) == expected
    assert expected['installedVersions']['whab_plugin_web']
    assert set(expected['controllerSha256']) == {'sidebar.js', 'amount-privacy.js', 'money-display.js'}
    for page in expected['pages']:
        html = (tmp_path / page).read_text()
        theme = 'light' if '-light.' in page else 'dark'
        assert f'data-theme="{theme}"' in html
        assert 'data-fictional-static-preview="true"' in html
        assert 'connect-src \'none\'' in html and 'form-action \'none\'' in html
        assert not re.search(r'<script[^>]+src=', html)
        assert not re.search(r'<link[^>]+href="/', html)
        assert set(re.findall(r'data-shipped-controller="([^"]+)"', html)) == {
            'sidebar.js', 'amount-privacy.js', 'money-display.js',
        }
        assert 'data-amount-privacy-toggle' in html and 'data-sidebar-toggle' in html
        for account in expected['accounts']:
            identifier = account['id']
            sidebar = re.search(
                rf'<a class="sidebar-account"[^>]*data-account-id="{identifier}"[^>]*>(.*?)</a>',
                html, re.S,
            )[1]
            assert f'<span data-private-amount>{account["sidebarAmount"]}</span>' in sidebar
            assert f'title="{account["statusLabel"]}"' in sidebar
            if page.startswith('accounts-'):
                row = re.search(rf'<form[^>]+data-inline-account="{identifier}"[^>]*>(.*?)</form>', html, re.S)[1]
                assert f'<span data-private-amount data-private-currency="{account["currency"]}">{account["accountAmount"]}</span>' in row
                assert f'<small>{account["statusLabel"]}</small>' in row
        if page.startswith('accounts-'):
            for currency, amounts in expected['totals'].items():
                summary = re.search(rf'<dl[^>]+aria-label="Account totals in {currency}"[^>]*>(.*?)</dl>', html, re.S)[1]
                for label, key in [('Assets', 'assets'), ('Liabilities', 'liabilities'), ('Net worth', 'netWorth')]:
                    assert f'<dt>{label} · {currency}</dt><dd><span data-private-amount>{amounts[key]}</span></dd>' in summary
        else:
            transaction = expected['transactions'][0]
            row = re.search(r'<tr[^>]+data-transaction-id="fictional-purchase"[^>]*>(.*?)</tr>', html, re.S)[1]
            assert 'data-money-rounded data-transaction-detail="amount"' in row
            assert f'value="{transaction["editorAmount"]}"' in row
            assert 'data-amount="123456"' in html
            assert transaction['displayAmount'] == '-1,235'
            assert transaction['hiddenAmount'] == '$X,XXX'
    # Exported input balances must not be normalized to the presentation sign.
    snapshot = builder['fictional_snapshot']()
    balances = {row['id']: row['balance'] for row in snapshot['accounts']}
    assert balances['loan'] == 500000
    assert balances['ynab-card'] == -450000
    assert balances['overpaid'] == -7500
    assert snapshot['transactions'][0]['amount'] == 123456
