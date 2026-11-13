"""Actual console arguments, HTTPS host, worker storage and restart replay."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest

from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_real_transfer_workflow_uses_fictional_home_and_exact_restart_retry(tmp_path, kind):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '', 'WHAB_PLUGINS': '', 'WHAB_STORAGE': kind, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    store = open_storage(load_plugins((kind,)).storage_plugin(kind), StorageContext(home, 'sandbox', {}))
    try:
        ids = []
        for name, amount in [('旅行 🍏', 12345), ('Сбережения', -12345)]:
            account = store.add_account('personal', {'name': name, 'type': 'depository', 'balance': 100000})
            ids.append(store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional', 'amount': amount, 'categoryId': None, 'note': '[YNAB]\nExact note  '}))
    finally:
        store.close()
    executable = Path(sys.executable).parent / 'whab'
    def run(*args, ok=True):
        result = subprocess.run([str(executable), '--environment', 'sandbox', *args], env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)
    for action in ['transfers', 'match-transfer', 'unmatch-transfer']:
        result = subprocess.run([str(executable), '--profile', '/missing', 'Fictional', 'trnx', action, '--help'], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and 'Exact successful retries' in result.stdout and 'reviewed=true' in result.stdout
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        pairs = run('My budget', 'trnx', 'transfers', '--account', '旅行 🍏', '--since', '2026-11-01')
        assert pairs['data']['total'] == 1
        preview = run('default', 'trnx', 'match-transfer', *ids, '--dry-run')
        saved = run('My budget', 'trnx', 'match-transfer', *ids, '--revision', preview['revision'], '--fingerprint', preview['data']['fingerprint'], '--agent', 'Codex', '--batch', 'fictional', '--home', str(home))
        assert saved['data'] == preview['data']
        retry_args = ('default', 'trnx', 'match-transfer', *ids, '--revision', saved['expectedRevision'], '--fingerprint', saved['fingerprint'], '--request-id', saved['requestId'], '--agent', 'Codex', '--batch', 'fictional')
        assert run(*retry_args)['data'] == saved['data']
        assert run('default', 'trnx', 'transfers')['data']['total'] == 0
        rows = run('default', 'trnx', 'list')['data']
        assert all(row['transferPeerId'] and not row['reviewed'] and row['note'] == '[YNAB]\nExact note  ' for row in rows)
        other = run('budget', 'create', 'Fictional isolated')['id']
        assert 'missing' in run(other, 'trnx', 'match-transfer', *ids, ok=False)['error']
    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run(*retry_args)['data'] == saved['data']
        history = run('default', 'history', 'list')['data']['entries']
        assert sum(row['requestId'] == saved['requestId'] for row in history) == 1
        assert run('default', 'trnx', 'unmatch-transfer', *ids)['data']['action'] == 'unmatch'
