"""Real console → HTTPS host → native storage classification, zero review and replay."""

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
from whab.plugins import StorageContext, WorkspaceImport, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize('storage', ['sqlite', 'json'])
def test_opening_commands_native_zero_review_and_restart_replay(tmp_path, storage):
    home = tmp_path / 'runtime'
    env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG') if key in os.environ}
    env.update(HOME=str(tmp_path), WHAB_HOME=str(home), WHAB_UI='', WHAB_PLUGINS='',
               WHAB_STORAGE=storage, ALLOW_LIVE_PLAID='false')
    executable = Path(sys.executable).parent / 'whab'
    month = date.today().strftime('%Y-%m')
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    native = open_storage(load_plugins((storage,)).storage_plugin(storage), StorageContext(home, 'sandbox', {}))
    account = {'id': 'fictional-cash', 'name': 'Fictional cash', 'institution': 'Fixture',
               'type': 'depository', 'balance': 5000, 'source': 'fixture',
               'stale': False, 'balanceUnknown': False}
    category = {'id': 'initial', 'name': '初期 🍏', 'group': 'Finance', 'target': 0}
    rows = tuple({'id': identifier, 'accountId': account['id'], 'date': month + '-01',
        'merchant': '  Existing fictional cash  ', 'amount': amount, 'categoryId': 'initial',
        'pending': False, 'reviewed': False, 'source': 'fixture', 'note': ' [YNAB: cleared]\nKeep  ',
        'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': False, 'deleted': False}
        for identifier, amount in [('cash-opening', -5000), ('zero-opening', 0)])
    try:
        native.import_workspace('personal', 'fictional', 'opening-cli', WorkspaceImport(
            accounts=(account,), categories=(category,), transactions=rows,
            assignments=(), budget_start_month=month))
    finally:
        native.close()

    def run(*args, ok=True):
        result = subprocess.run([str(executable), '--environment', 'sandbox', *args],
            env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def query(operation):
        return run('api', '--budget', 'default', 'query', operation)

    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        before = query('portable')['data']
        created = run('My budget', 'category', 'create', 'Opening Balance', '--parent', 'Finance',
                      '--type', 'opening_balance', '--agent', 'Codex')
        assert created['data']['kind'] == 'opening_balance'
        preview_before = query('portable')['data']
        preview = run('default', 'category', 'classify', 'Finance / 初期 🍏', '--type', 'opening_balance', '--dry-run')
        assert preview['dryRun'] and preview['data']['after']['kind'] == 'opening_balance'
        assert preview['data']['impacts'] and 'openingFunding' in json.dumps(preview['data']['impacts'])
        assert query('portable')['data'] == preview_before
        saved = run('My budget', 'category', 'classify', 'initial', '--type', 'opening_balance',
                    '--agent', 'Codex', '--batch', 'opening-fixture')
        assert saved['data'] == {**category, 'currency': 'USD', 'parentId': saved['data']['parentId'], 'kind': 'opening_balance'}
        replay_args = ('default', 'category', 'classify', 'initial', '--type', 'opening_balance',
            '--agent', 'Codex', '--batch', 'opening-fixture', '--request-id', saved['requestId'],
            '--revision', saved['expectedRevision'])
        assert run(*replay_args)['replayed'] is True
        run('default', 'trnx', 'categorize', 'cash-opening', 'zero-opening',
            '--category', 'Finance / Opening Balance', '--agent', 'Codex')
        # The zero record is accepted only as an existing annotation target, not a new financial amount.
        run('default', 'trnx', 'review', 'cash-opening', 'zero-opening', '--reviewed', '--agent', 'Codex')
        actual = query('workspace')['data']['summary']
        assert (actual['income'], actual['spent'], actual['openingFunding'], actual['readyToAssign']) == (0, 0, 5000, 5000)
        after = query('portable')['data']
        expected = [{**row, 'categoryId': created['data']['id'], 'categoryEdited': True,
                     'categoryAgent': 'Codex', 'reviewed': True} for row in before['transactions']]
        assert after['transactions'] == expected
        assert after['accounts'] == before['accounts']
        assert after['assignments'] == before['assignments']
        assert run('default', 'trnx', 'review-audit')['data'] == []
        listed = run('default', 'trnx', 'list')
        assert listed['complete'] and len(listed['data']) == 2
        assert all(row['categoryKind'] == 'opening_balance' for row in listed['data'])
        assert run('default', 'category', 'create', 'Invalid', '--parent', 'Finance',
                   '--type', 'opening_balance', '--target', '1', ok=False)['error']
        assert query('portable')['data'] == after

    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run(*replay_args)['replayed'] is True
        assert query('portable')['data'] == after


@pytest.mark.parametrize('args', [[], ['Fictional', 'category'], ['Fictional', 'category', 'create'],
    ['Fictional', 'category', 'classify'], ['Fictional', 'trnx', 'review']])
def test_installed_opening_help_is_offline(tmp_path, args):
    home = tmp_path / 'must-not-be-created'
    result = subprocess.run([str(Path(sys.executable).parent / 'whab'), *args, '--help'],
        env={'PATH': os.environ.get('PATH', ''), 'HOME': str(tmp_path), 'WHAB_HOME': str(home)},
        text=True, capture_output=True, timeout=30)
    assert result.returncode == 0
    assert 'Opening Balance' in result.stdout
    assert not home.exists()
