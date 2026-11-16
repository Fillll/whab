"""Real console, HTTPS host and native SQLite/JSON void/unvoid with restart retry."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest

from whab.domain.history import financial
from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


def fixture_snapshot(store):
    snapshot = store.export_budget('personal')
    snapshot['accounts'] = [
        {'id': identifier, 'name': name, 'type': kind, 'balance': 100000, 'currency': 'USD', 'source': 'manual',
         'institution': 'Fictional', 'onBudget': True, 'stale': False, 'balanceUnknown': False}
        for identifier, name, kind in [('cash', '旅行 🍏', 'depository'), ('card', 'Карта', 'credit')]]
    snapshot['categories'] = [{'id': 'expense', 'name': 'Fictional expense', 'group': 'Fictional', 'kind': 'expense', 'target': 0, 'currency': 'USD'}]
    snapshot['transactions'] = []
    for identifier, account, amount, source in [('imported', 'cash', -90000, 'ynab'),
        ('manual', 'cash', 1234, 'manual'), ('zero', 'cash', 0, 'manual'),
        ('pair-out', 'cash', 500, 'manual'), ('pair-in', 'card', -500, 'manual'),
        ('untouched', 'cash', 777, 'manual'), ('provider-removed', 'cash', 100, 'ynab')]:
        paired = identifier.startswith('pair-')
        row = {'id': identifier, 'accountId': account, 'date': '2026-11-01', 'merchant': '  Fictional merchant  ',
            'merchantLogo': None, 'amount': amount, 'categoryId': None if paired else 'expense', 'pending': False,
            'reviewed': True, 'source': source, 'note': '[YNAB] exact\n  retained ', 'isTransfer': paired,
            'inferredTransfer': False, 'categoryEdited': False, 'deleted': False}
        if paired:
            row['transferPeerId'] = 'pair-in' if identifier == 'pair-out' else 'pair-out'
        if identifier == 'provider-removed':
            row.update(deleted=True, reviewed=False, voided=True, providerDeleted=True)
        snapshot['transactions'].append(row)
    snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
    return snapshot


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_fictional_console_void_unvoid_atomicity_and_restart(tmp_path, kind):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '', 'WHAB_PLUGINS': '',
           'WHAB_ENV': 'sandbox', 'WHAB_STORAGE': kind, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    from whab_plugin_sqlite import Store
    seed = Store(':memory:')
    try:
        snapshot = fixture_snapshot(seed)
    finally:
        seed.close()
    store = open_storage(load_plugins((kind,)).storage_plugin(kind), StorageContext(home, 'sandbox', {}))
    try:
        store.restore_budget('personal', snapshot)
    finally:
        store.close()
    profile = tmp_path / 'fictional.budget'
    profile.write_text(json.dumps({'home': str(home), 'environment': 'sandbox'}))
    executable = Path(sys.executable).parent / 'whab'
    def run(*parts, ok=True, table=False):
        result = subprocess.run([str(executable), '--profile', str(profile), *parts],
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return result.stdout if table else json.loads(result.stdout if ok else result.stderr)
    for action in ['void', 'unvoid']:
        help_result = subprocess.run([str(executable), '--profile', '/missing', 'Fictional', 'trnx', action, '--help'],
                                     env=env, capture_output=True, text=True, timeout=30)
        assert help_result.returncode == 0
        assert all(word in help_result.stdout for word in ['balanceChanges', 'tombstones', 'manual', '--fingerprint', 'uncertain'])
    selected = ['imported', 'manual', 'zero', 'pair-out', 'pair-in']
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        before = run('default', 'trnx', 'list')
        history_before = run('default', 'history', 'list')['data']['entries']
        preview = run('My budget', 'trnx', 'void', *selected, '--dry-run')
        assert preview['revision'] == before['revision'] and preview['data']['count'] == 5
        assert preview['data']['balanceChanges'] == [
            {'accountId': 'cash', 'currency': 'USD', 'accountType': 'depository', 'before': 100000, 'after': 101734},
            {'accountId': 'card', 'currency': 'USD', 'accountType': 'credit', 'before': 100000, 'after': 100500}]
        assert run('default', 'trnx', 'list') == before
        assert 'missing' in run('default', 'trnx', 'void', 'manual', 'missing', ok=False)['error'].lower()
        assert any(word in run('default', 'trnx', 'void', 'pair-out', ok=False)['error'].lower() for word in ['both', 'peer', 'pair'])
        assert 'preview changed' in run('default', 'trnx', 'void', *selected, '--revision', 'stale', ok=False)['error']
        assert run('default', 'trnx', 'list') == before
        saved = run('My budget', 'trnx', 'void', *selected, '--revision', preview['revision'],
            '--fingerprint', preview['data']['fingerprint'], '--agent', 'Codex', '--batch', 'fictional-correction',
            '--request-id', 'void-intent', '--environment', 'sandbox')
        assert saved['data'] == preview['data']
        for row in saved['data']['changes']:
            assert row['after']['voided'] and row['after']['deleted'] and not row['after']['reviewed']
            for field in ['id', 'accountId', 'amount', 'date', 'merchant', 'note', 'source', 'categoryId', 'transferPeerId']:
                assert row['before'].get(field) == row['after'].get(field)
        live = run('default', 'trnx', 'list')
        assert [row['id'] for row in live['data']] == ['untouched'] and live['revision'] != before['revision']
        retry = ('default', 'trnx', 'void', *selected, '--revision', saved['expectedRevision'], '--fingerprint', saved['fingerprint'],
                 '--request-id', saved['requestId'], '--agent', 'Codex', '--batch', 'fictional-correction')
        assert run(*retry)['data'] == saved['data']
        assert 'Request ID:' in run(*retry, '--format', 'table', table=True)
        history = run('default', 'history', 'list')['data']['entries']
        assert len(history) == len(history_before) + 1
        assert sum(entry['requestId'] == saved['requestId'] for entry in history) == 1
        other = run('budget', 'create', 'Fictional isolated')['id']
        assert 'not found' in run(other, 'trnx', 'unvoid', *selected, ok=False)['error'].lower()
        restore = run('default', 'trnx', 'unvoid', *selected, '--dry-run')
        assert all(not row['after']['reviewed'] and not row['after']['deleted'] and row['after']['voided'] is False for row in restore['data']['changes'])
        assert 'provider' in run('default', 'trnx', 'unvoid', 'provider-removed', ok=False)['error'].lower()
        assert run('default', 'trnx', 'list')['revision'] == restore['revision']
    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run(*retry)['data'] == saved['data']
        assert len(run('default', 'history', 'list')['data']['entries']) == len(history)
        conflict = run('default', 'trnx', 'unvoid', *selected, '--revision', saved['expectedRevision'],
                       '--fingerprint', saved['fingerprint'], '--request-id', saved['requestId'], '--agent', 'Codex',
                       '--batch', 'fictional-correction', ok=False)
        assert any(word in conflict['error'].lower() for word in ['request', 'conflict'])
        restored = run('default', 'trnx', 'unvoid', *selected, '--agent', 'Codex', '--batch', 'restoration')
        assert all(row['after'] == 100000 for row in restored['data']['balanceChanges'])
        rows = run('default', 'trnx', 'list')['data']
        assert {row['id'] for row in rows} == set(selected) | {'untouched'}
        assert all(not row['reviewed'] for row in rows if row['id'] in selected)
        assert next(row for row in rows if row['id'] == 'untouched')['reviewed']
