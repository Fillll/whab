"""Real CLI category deletions are audited, safe and durable on both stores."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch
from uuid import uuid4

import pytest

from whab.plugins.local import local_connection


@pytest.mark.parametrize('storage', ['sqlite', 'json'])
def test_real_console_safe_deletion_history_and_reopen(tmp_path, storage):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '',
           'WHAB_PLUGINS': '', 'WHAB_STORAGE': storage, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    executable = Path(sys.executable).parent / 'whab'

    def cli(*args, ok=True):
        result = subprocess.run([str(executable), *args], env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def run(*args, ok=True):
        return cli('--environment', 'sandbox', *args, ok=ok)

    for args in [('delete',), ('group', 'delete')]:
        result = subprocess.run([str(executable), '--profile', '/missing/fictional-profile', 'My budget', 'category', *args, '--help'],
                                env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and 'including zero-valued rows' in result.stdout and not home.exists()

    def wait_closed():
        endpoint = home / 'run/sandbox/host.json'
        deadline = monotonic() + 20
        while endpoint.exists() and monotonic() < deadline:
            sleep(.05)
        assert not endpoint.exists()

    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox') as owner:
        assert owner.get('/api/budget').json()['storage'] == storage
        assert any(row['id'] == 'default' for row in run('budget', 'list')['budgets'])
        other = run('budget', 'create', 'Fictional isolated')['id']
        pristine = run(other, 'category', 'list')['data']
        parent = run('My budget', 'category', 'group', 'create', 'Fictional 🏡 обслуживание')['data']
        leaf = run('default', 'category', 'create', 'Fictional 家', '--parent', parent['id'], '--target', '1234')['data']

        def advanced(name, payload):
            revision = run('default', 'category', 'list')['revision']
            return run('api', '--budget', 'default', 'command', name, '--parameters', json.dumps(payload),
                       '--revision', revision, '--request-id', uuid4().hex)['data']

        cash = advanced('add_account', {'name': 'Fictional cash', 'type': 'depository', 'balance': 100000})
        protected = run('default', 'category', 'create', 'Fictional referenced', '--parent', 'Goals')['data']
        advanced('add_transaction', {'accountId': cash, 'date': '2026-08-01', 'merchant': 'Fictional invoice',
                                    'amount': 100, 'categoryId': protected['id'], 'note': 'Preserve annotation'})
        assigned = run('default', 'category', 'create', 'Fictional assigned', '--parent', 'Goals')['data']
        advanced('assign', {'categoryId': assigned['id'], 'month': '2026-11', 'assigned': 0})
        status = run('My budget', 'category', 'show', assigned['id'])['data']['deletion']
        assert status == {'categoryId': assigned['id'], 'deletable': False,
                          'transactions': {'total': 0, 'live': 0, 'deleted': 0},
                          'assignments': {'count': 1, 'months': ['2026-11'], 'zeroMonths': ['2026-11']}}
        before = run('api', '--budget', 'default', 'query', 'portable')['data']
        assert 'children' in run('My budget', 'category', 'group', 'delete', parent['id'], ok=False)['error']
        for record in [protected, assigned]:
            error = run('default', 'category', 'delete', record['id'], ok=False)['error']
            assert 'No automatic retry' in error and 'monthly assignments' in error
            assert 'refused; no deletion was saved' in error and 'may have been saved' not in error
        assert run('api', '--budget', 'default', 'query', 'portable')['data'] == before
        profile = tmp_path / 'fictional.budget'
        profile.write_text(json.dumps({'home': str(home), 'environment': 'sandbox'}))
        deleted = cli('--profile', str(profile), 'My budget', 'category', 'delete', 'fictional 🏡 обслуживание / fictional 家',
                      '--ca', str(home / 'tls/ca.crt'))
        assert deleted['data'] == {**leaf, 'deleted': True} and deleted['budgetId'] == 'default'
        assert run('default', 'category', 'list', '--parent', parent['id'])['data'][0]['children'] == []
        entry = run('default', 'history', 'list')['data']['entries'][0]
        assert entry['operation'] == 'delete_category' and entry['requestId'] == deleted['requestId']
        preview = run('default', 'history', 'undo', entry['id'])
        run('default', 'history', 'undo', entry['id'], '--apply', preview['data']['fingerprint'])
        assert any(row['id'] == leaf['id'] for group in run('default', 'category', 'list')['data'] for row in group['children'])
        preview = run('default', 'history', 'redo', entry['id'])
        run('default', 'history', 'redo', entry['id'], '--apply', preview['data']['fingerprint'])
        removed_parent = run('My budget', 'category', 'group', 'delete', parent['id'])
        assert removed_parent['data'] == {**parent, 'deleted': True}
        # Protect referenced peers while allowing a separate genuinely empty parent to be deleted.
        empty = run('default', 'category', 'group', 'create', 'Fictional empty')['data']
        removed = cli('default', 'category', 'group', 'delete', 'fictional EMPTY', '--environment', 'sandbox', '--home', str(home))
        assert removed['data'] == {**empty, 'deleted': True}
        disposable = run('default', 'category', 'create', 'Fictional last child', '--parent', 'Goals')['data']
        run('default', 'category', 'delete', disposable['id'])
        after = run('api', '--budget', 'default', 'query', 'portable')['data']
        for key in ['transactions', 'assignments', 'accounts']:
            assert after[key] == before[key]
        assert after['history']['entries'][:len(before['history']['entries'])] == before['history']['entries']
        assert run(other, 'category', 'list')['data'] == pristine
        tree = run('default', 'category', 'list')['data']
        assert not any(row['id'] == empty['id'] for row in tree)
        assert not any(row['id'] == parent['id'] for row in tree)
        assert not any(row['id'] == leaf['id'] for group in tree for row in group['children'])
    wait_closed()
    # Reopen the actual saved original budget through a new console process/host.
    assert run('My budget', 'category', 'list')['data'] == tree
    assert run('api', '--budget', 'default', 'query', 'portable')['data'] == after
    wait_closed()
