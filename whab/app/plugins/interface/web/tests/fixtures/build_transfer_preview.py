"""Render isolated, static transfer UI acceptance fixtures from shipped templates.

Run from whab/app with the development dependencies installed:
  .venv/bin/python plugins/interface/web/tests/fixtures/build_transfer_preview.py \
    --output /tmp/whab-transfer-ui-static

The generated pages contain fictional data only. They run the shipped category
option hydration helper; persistence and navigation are disabled. Native details
provide the actual funding explanations. No HTTP runtime or production store is
opened. Serve the output directory with a loopback static server for browser QA.
"""

import argparse
import base64
import re
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.testclient import TestClient
from whab_plugin_sqlite import Store
from whab_plugin_web.plugin import plugin as web_plugin

from whab.app import create_app
from whab.config import Settings
from whab.plugins import PluginRegistry


def build(output):
    app_root = Path(__file__).resolve().parents[5]
    with TemporaryDirectory(prefix='whab-fictional-transfer-preview-') as directory:
        store = Store(':memory:', month='2026-10')
        checking = store.add_account('personal', {'name': 'Fictional checking', 'type': 'depository', 'balance': 100000})
        savings = store.add_account('personal', {'name': 'Fictional archived savings', 'type': 'depository', 'balance': 0, 'onBudget': False})
        expense = store.add_category('personal', {'group': 'Fictional living', 'name': 'Groceries', 'target': 0})
        store.add_category('personal', {'group': 'Fictional finance', 'name': 'Between accounts', 'kind': 'transfer', 'target': 0})
        out = store.add_transaction('personal', {'accountId': checking, 'date': '2026-10-31', 'merchant': 'Fictional matched outgoing', 'amount': 12345, 'categoryId': None})
        incoming = store.add_transaction('personal', {'accountId': savings, 'date': '2026-11-01', 'merchant': 'Fictional matched incoming', 'amount': -12345, 'categoryId': None})
        store.add_transaction('personal', {'accountId': checking, 'date': '2026-10-30', 'merchant': 'Fictional unmatched purchase', 'amount': 2100, 'categoryId': expense})
        store.add_transaction('personal', {'accountId': checking, 'date': '2026-10-29', 'merchant': 'Fictional unclassified item', 'amount': 1500, 'categoryId': None})
        store.update_account('personal', savings, {'closed': True})
        pairs = [{'outId': out, 'inId': incoming}]
        preview = store.preview_transfer_matches('personal', pairs)
        store.apply_transfer_matches('personal', pairs, fingerprint=preview['fingerprint'])
        settings = Settings(Path(directory), 5173, 'sandbox', '', '', '', False, '', '', ())
        pages = {}
        with TestClient(create_app(store=store, settings=settings, plugins=PluginRegistry((web_plugin,))), base_url='https://127.0.0.1:5173') as client:
            for page, route in [('transactions', 'transactions/2026-10'), ('budget', 'budget/2026-10/usd')]:
                response = client.get('/budgets/home/' + route)
                response.raise_for_status()
                html = response.text
                for link, src in re.findall(r'(<link[^>]+href="([^"]+)"[^>]*>)', html):
                    if '.css' in src or '.svg' in src:
                        asset = client.get(src)
                        asset.raise_for_status()
                        replacement = '<style>' + asset.text + '</style>' if '.css' in src else link.replace(src, 'data:image/svg+xml;base64,' + base64.b64encode(asset.content).decode())
                        html = html.replace(link, replacement)
                for src in re.findall(r'<img[^>]+src="([^"]+\.svg(?:\?[^"]*)?)"', html):
                    asset = client.get(src)
                    asset.raise_for_status()
                    html = html.replace(src, 'data:image/svg+xml;base64,' + base64.b64encode(asset.content).decode())
                pages[page] = html
        store.close()
    source = (app_root / 'plugins/interface/web/src/whab_plugin_web/static/transaction-register.js').read_text()
    start = source.index('export function hydrateTransactionCategories')
    end = source.index('\nfunction categoryAllowsReview', start)
    code = source[start:end].replace('export function', 'function')
    script = '''<script>''' + code + '''
const template = document.querySelector('[data-transaction-categories]');
for (const name of ['focusin', 'pointerdown', 'keydown']) document.addEventListener(name, event => {
  if (event.target.matches('select[data-category-options-lazy]')) hydrateTransactionCategories(event.target, template);
});
document.addEventListener('submit', event => event.preventDefault());
document.addEventListener('click', event => {if (event.target.closest('a')) event.preventDefault();});
const note = document.createElement('p');
note.textContent = 'Fictional static preview · Inspect category choices and funding explanations; saves and navigation disabled.';
note.style.cssText = 'position:fixed;bottom:0;right:0;padding:8px;background:#ffdc35;color:#111;z-index:10000';
document.body.append(note);
</script></body>'''
    output.mkdir(parents=True, exist_ok=True)
    for page, original in pages.items():
        for theme in ('light', 'dark'):
            html = re.sub(r'<script\b[^>]*>.*?</script>', '', original, flags=re.S)
            html = html.replace('data-theme="dark"', f'data-theme="{theme}"', 1)
            html = html.replace('<body', '<body data-fictional-static-preview="true"', 1)
            html = html.replace('</body>', script)
            (output / f'{page}-{theme}.html').write_text(html)
    links = ''.join(f'<li><a href="{page}-{theme}.html">{page.title()} · {theme}</a></li>' for page in pages for theme in ('light', 'dark'))
    (output / 'index.html').write_text('<!doctype html><title>Fictional WHAB transfer preview</title><h1>Fictional WHAB transfer preview</h1><p>Category choices and dated funding; no saves or production data.</p><ul>' + links + '</ul>')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--output', type=Path, required=True, help='Directory for four fictional HTML previews and an index; use an isolated temporary directory.')
    args = parser.parse_args()
    build(args.output)
    print(f'Fictional static previews written to {args.output}')
