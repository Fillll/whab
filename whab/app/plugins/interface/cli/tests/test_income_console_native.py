"""Real CLI → HTTPS host → worker/native storage Income classification and replay."""

import json
import os
import subprocess
import sys
from datetime import date
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest

from whab.initialization import initialize_home
from whab.plugins import LedgerService, StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize('storage', ['sqlite', 'json'])
def test_income_commands_native_and_restart_replay(tmp_path, storage):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '',
           'WHAB_PLUGINS': '', 'WHAB_STORAGE': storage, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    executable = Path(sys.executable).parent / 'whab'
    month = date.today().strftime('%Y-%m')
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    native = open_storage(load_plugins((storage,)).storage_plugin(storage), StorageContext(home, 'sandbox', {}))
    try:
        service = LedgerService(native)
        account = service.add_account('personal', {'name': 'Fictional cash', 'type': 'depository', 'balance': 0})
        transaction = service.add_transaction('personal', {'accountId': account, 'date': month + '-01',
            'merchant': 'Fictional employer', 'amount': -500000, 'categoryId': None, 'note': ' [YNAB]\nKeep  '})
        service.add_category('personal', {'name': 'Fees', 'group': 'Finance', 'target': 0})
    finally:
        native.close()
    def run(*args, ok=True):
        result = subprocess.run([str(executable), '--environment', 'sandbox', *args], env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)
    def query(name):
        return run('api', '--budget', 'default', 'query', name)
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        before = query('portable')['data']
        created = run('My budget', 'category', 'create', '給与 🍏', '--parent', 'Finance', '--type', 'income', '--agent', 'Codex', '--batch', 'income-fixture')
        category = created['data']['id']
        assert created['data']['kind'] == 'income'
        run('default', 'category', 'classify', category, '--type', 'expense')
        prior = query('portable')['data']
        preview = run('default', 'category', 'classify', 'Finance / 給与 🍏', '--type', 'income', '--dry-run')
        assert preview['dryRun'] is True and preview['data']['after']['kind'] == 'income'
        assert query('portable')['data'] == prior
        saved = run('default', 'category', 'classify', '給与 🍏', '--type', 'income', '--agent', 'Codex', '--batch', 'income-fixture')
        assert saved['data']['id'] == category and saved['data']['kind'] == 'income'
        replay_args = ('default', 'category', 'classify', category, '--type', 'income', '--agent', 'Codex',
            '--batch', 'income-fixture', '--request-id', saved['requestId'], '--revision', saved['expectedRevision'])
        assert run(*replay_args)['replayed'] is True
        run('default', 'trnx', 'categorize', transaction, '--category', 'Finance / 給与 🍏', '--agent', 'Codex')
        run('default', 'trnx', 'review', transaction, '--reviewed', '--agent', 'Codex')
        actual = query('workspace')['data']
        assert (actual['summary']['income'], actual['summary']['classifiedIncome'], actual['summary']['readyToAssign']) == (500000, 500000, 500000)
        after = query('portable')['data']
        original = before['transactions'][0]
        assert after['transactions'] == [{**original, 'categoryId': category, 'categoryEdited': True, 'categoryAgent': 'Codex', 'reviewed': True}]
        assert after['assignments'] == before['assignments']
        assert run('default', 'category', 'classify', 'missing', '--type', 'income', ok=False)['error']
        run('default', 'category', 'create', 'Invalid', '--parent', 'Finance', '--type', 'income', '--target', '1', ok=False)
    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run(*replay_args)['replayed'] is True
        assert query('portable')['data'] == after
