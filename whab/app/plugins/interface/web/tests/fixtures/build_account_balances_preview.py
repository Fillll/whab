"""Render fictional signed balances using the installed WHAB Web templates.

Run with WHAB, SQLite storage and Web installed (development or installed app):
  python build_account_balances_preview.py --output /tmp/whab-account-preview

The output contains Accounts and Transactions pages in light and dark themes,
plus expected-values.json for browser acceptance at desktop and compact widths.
Only the shipped privacy, sidebar and exact-money display controllers run. Saves, navigation and
network requests are disabled; no running WHAB server or production budget is
opened. Serve the output directory with a loopback static server for browser QA.
"""

import argparse
import base64
import hashlib
import json
import re
from importlib.metadata import version
from importlib.resources import files
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.domain.ledger_state import portable_snapshot
from whab.plugins import PluginRegistry

# Expected values are deliberate examples, not calculated by the formatter under test.
CASES = (
    ('checking', 'Fictional checking', 'depository', 1000000, 'manual', False, False, 'USD', '$10,000', 'current'),
    ('loan', 'Fictional loan', 'loan', 500000, 'manual', False, False, 'USD', '-$5,000', 'current'),
    ('mortgage', 'Fictional mortgage', 'loan', 2500000, 'manual', False, False, 'USD', '-$25,000', 'current'),
    ('card', 'Fictional credit card', 'credit', 125075, 'manual', False, False, 'USD', '-$1,251', 'current'),
    ('overpaid', 'Fictional overpaid card', 'credit', -7500, 'manual', False, False, 'USD', '$75', 'current'),
    ('overdraft', 'Fictional overdraft', 'depository', -25000, 'manual', False, False, 'USD', '-$250', 'current'),
    ('zero', 'Fictional zero card', 'credit', 0, 'manual', False, False, 'USD', '$0', 'current'),
    ('unknown', 'Fictional unavailable card', 'credit', 123456, 'bank', True, True, 'USD', None, 'unavailable'),
    ('stale', 'Fictional stale loan', 'loan', 234567, 'bank', True, False, 'USD', '-$2,346', 'stale'),
    ('ynab-card', 'Fictional imported card', 'credit', -450000, 'ynab', True, True, 'USD', '-$4,500', 'estimated'),
    ('ynab-loan', 'Fictional legacy imported loan', 'loan', -900000, 'ynab', False, False, 'USD', '-$9,000', 'estimated'),
    ('ynab-overpaid', 'Fictional imported overpayment', 'credit', 23000, 'ynab', False, False, 'USD', '$230', 'estimated'),
    ('other-debt', 'Fictional other debt', 'other_liability', 34000, 'manual', False, False, 'USD', '-$340', 'current'),
    ('eur-cash', 'Fictional euro checking', 'depository', 250000, 'manual', False, False, 'EUR', 'EUR 2,500', 'current'),
    ('eur-loan', 'Fictional euro loan', 'loan', 150000, 'manual', False, False, 'EUR', '-EUR 1,500', 'current'),
)
STATUS_LABELS = {
    'current': 'Current balance',
    'stale': 'Last known balance',
    'estimated': 'Estimated from imported transactions · unverified',
    'unavailable': 'Balance unavailable',
}
TOTALS = {
    'USD': {'assets': '$10,075', 'liabilities': '-$31,841', 'netWorth': '-$21,766'},
    'EUR': {'assets': 'EUR 2,500', 'liabilities': '-EUR 1,500', 'netWorth': 'EUR 1,000'},
}


def fictional_snapshot():
    """Create a public portable fixture; source balances stay exactly as supplied."""
    accounts = {}
    for identifier, name, kind, balance, source, stale, unknown, currency, _, _ in CASES:
        accounts[identifier] = {
            'id': identifier, 'name': name, 'institution': 'Fictional institution',
            'type': kind, 'balance': balance, 'source': source, 'currency': currency,
            'stale': stale, 'balanceUnknown': unknown,
            'onBudget': kind in {'depository', 'credit'},
            'subtype': 'mortgage' if identifier == 'mortgage' else None,
        }
    state = {
        'budgetStartMonth': '2026-11', 'accounts': accounts, 'categories': {},
        'transactions': {
            'fictional-purchase': {
                'id': 'fictional-purchase', 'accountId': 'card', 'date': '2026-11-15',
                'merchant': 'Fictional stationery', 'merchantLogo': None, 'amount': 123456,
                'categoryId': None, 'pending': False, 'reviewed': False, 'source': 'bank',
                'note': 'Fictional example only', 'isTransfer': False,
                'inferredTransfer': False, 'categoryEdited': False, 'deleted': False,
            },
        },
        'budget': {},
    }
    return portable_snapshot(state)


def expected_values():
    """Machine-readable, stable selectors and independently specified expectations."""
    return {
        'fictional': True,
        'viewports': [{'width': 1440, 'height': 1000}, {'width': 390, 'height': 844}],
        'pages': [f'{page}-{theme}.html' for page in ('accounts', 'transactions') for theme in ('light', 'dark')],
        'accounts': [
            {
                'id': identifier, 'name': name, 'storedBalance': balance, 'source': source,
                'currency': currency, 'status': status, 'statusLabel': STATUS_LABELS[status],
                'accountAmount': amount if amount is not None else 'Unavailable',
                'sidebarAmount': amount if amount is not None else '—',
                'hiddenAmount': '$X,XXX' if currency == 'USD' else f'{currency} X,XXX',
                'rowSelector': f'[data-inline-account="{identifier}"] [data-account-balance]',
                'sidebarSelector': f'.sidebar-account[data-account-id="{identifier}"] small',
            }
            for identifier, name, _, balance, source, _, _, currency, amount, status in CASES
        ],
        'transactions': [{
            'id': 'fictional-purchase', 'storedAmount': 123456, 'currency': 'USD',
            'displayAmount': '-1,235', 'editorAmount': '-1234.56', 'hiddenAmount': '$X,XXX',
            'rowSelector': '[data-transaction-id="fictional-purchase"]',
            'displaySelector': '[data-transaction-id="fictional-purchase"] [data-money-display]',
            'editorSelector': '[data-transaction-id="fictional-purchase"] input[data-money-rounded]',
        }],
        'totals': TOTALS,
        'checks': [
            'Accounts and sidebar agree on every signed balance and evidence label.',
            'Unknown, stale and all YNAB estimates are excluded from current totals.',
            'Privacy uses one fixed currency mask for debts, credits, zero and unavailable.',
            'Toggling privacy and the sidebar does not change account values or geometry.',
            'The purchase displays -1,235, reveals exact -1234.56 when editing, and keeps its spending sign.',
            'Compact and desktop layouts remain usable in both appearances.',
        ],
    }


def _inline_assets(client, html):
    for link, src in re.findall(r'(<link[^>]+href="([^"]+)"[^>]*>)', html):
        asset = client.get(src)
        asset.raise_for_status()
        replacement = '<style>' + asset.text + '</style>' if '.css' in src else link.replace(
            src, 'data:image/svg+xml;base64,' + base64.b64encode(asset.content).decode(),
        )
        html = html.replace(link, replacement)
    for src in re.findall(r'<img[^>]+src="([^"]+\.svg(?:\?[^"]*)?)"', html):
        asset = client.get(src)
        asset.raise_for_status()
        html = html.replace(src, 'data:image/svg+xml;base64,' + base64.b64encode(asset.content).decode())
    return re.sub(r'<script\b[^>]*>.*?</script>', '', html, flags=re.S)


def build(output):
    """Render installed resources in memory and emit read-only browser artifacts."""
    scripts = {
        name: files('whab_plugin_web').joinpath('static', name).read_text()
        for name in ('sidebar.js', 'amount-privacy.js', 'money-display.js')
    }
    pages = {}
    with TemporaryDirectory(prefix='whab-fictional-account-preview-') as directory:
        store = Store(':memory:', month='2026-11')
        try:
            store.restore_budget('personal', fictional_snapshot())
            before = store.export_budget('personal')
            settings = Settings(Path(directory), 5173, 'sandbox', '', '', '', False, '', '', ())
            app = create_app(store=store, settings=settings, plugins=PluginRegistry((web_plugin,)))
            with TestClient(app, base_url='https://127.0.0.1:5173') as client:
                for page, route in [('accounts', 'accounts'), ('transactions', 'transactions/2026-11')]:
                    response = client.get('/budgets/home/' + route)
                    response.raise_for_status()
                    pages[page] = _inline_assets(client, response.text)
            if store.export_budget('personal') != before:
                raise AssertionError('Rendering the fictional preview changed its ledger.')
        finally:
            store.close()
    safety = '''<script data-preview-safety>
document.addEventListener('submit', event => event.preventDefault(), true);
document.addEventListener('click', event => {
  if (event.target.closest('a')) event.preventDefault();
}, true);
window.fetch = () => {throw new Error('This fictional preview cannot call a runtime.');};
</script>'''
    policy = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; script-src \'unsafe-inline\'; style-src \'unsafe-inline\'; img-src data:; connect-src \'none\'; form-action \'none\'; base-uri \'none\'">'
    controllers = ''.join(f'<script data-shipped-controller="{name}">{code}</script>' for name, code in scripts.items())
    notice = '<p style="position:fixed;bottom:0;right:0;margin:0;padding:6px 10px;background:#ffdc35;color:#111;z-index:10000;font:12px system-ui">Fictional preview · Eye and sidebar controls work · Saves and navigation disabled</p>'
    output.mkdir(parents=True, exist_ok=True)
    for page, original in pages.items():
        for theme in ('light', 'dark'):
            html = original.replace('<head>', '<head>' + policy + safety + controllers, 1)
            html = html.replace('data-theme="dark"', f'data-theme="{theme}"', 1)
            html = html.replace('<body', '<body data-fictional-static-preview="true"', 1)
            (output / f'{page}-{theme}.html').write_text(html.replace('</body>', notice + '</body>'))
    expected = expected_values()
    expected['installedVersions'] = {name: version(name) for name in ('whab', 'whab_plugin_web', 'whab_plugin_sqlite')}
    expected['controllerSha256'] = {name: hashlib.sha256(code.encode()).hexdigest() for name, code in scripts.items()}
    (output / 'expected-values.json').write_text(json.dumps(expected, ensure_ascii=False, indent=2) + '\n')
    links = ''.join(f'<li><a href="{name}">{name}</a></li>' for name in expected['pages'])
    (output / 'index.html').write_text('<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><title>Fictional WHAB account balances</title><h1>Fictional account balances</h1><p>No production data or writes. Test at 1440px and 390px, with amounts shown and hidden.</p><ul>' + links + '</ul><p><a href="expected-values.json">Expected amounts, evidence labels and totals</a></p>')
    return expected


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='Isolated output directory for four fictional HTML pages, their index and expected-values.json; never use a runtime data directory.')
    args = parser.parse_args()
    build(args.output)
    print(f'Fictional static previews written to {args.output}')
