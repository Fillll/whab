"""Real short CLI, verified HTTPS and both native stores on fictional budgets."""

import json
import os
import subprocess
import sys
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import pytest
from whab_plugin_sqlite import Store

from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.local import local_connection
from whab.plugins.storage import open_storage


@pytest.mark.parametrize('storage', ['sqlite', 'json'])
def test_general_console_changes_split_restart_and_history(tmp_path, storage):
    home = tmp_path / 'fictional-runtime'
    env = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL') if key in os.environ}
    env.update(HOME=str(tmp_path), WHAB_HOME=str(home), WHAB_ENV='sandbox', WHAB_UI='', WHAB_PLUGINS='', WHAB_STORAGE=storage, ALLOW_LIVE_PLAID='false')
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    seed = Store(':memory:', month='2026-11')
    try:
        account = seed.add_account('personal', {'name': 'Fictional savings 🍏', 'type': 'depository', 'balance': 500000})
        category = seed.add_category('personal', {'name': 'Residence', 'group': 'Fictional home', 'target': 1000})
        component = seed.add_category('personal', {'name': 'Principal', 'group': 'Fictional finance', 'target': 0})
        original = seed.add_transaction('personal', {'accountId': account, 'date': '2024-05-15', 'merchant': 'Fictional receipt', 'amount': -100000, 'categoryId': category, 'note': '  [YNAB: cleared]\nOriginal source note  '})
        payment = seed.add_transaction('personal', {'accountId': account, 'date': '2024-05-17', 'merchant': 'Fictional purchase', 'amount': 42500, 'categoryId': category, 'note': 'Untouched payment'})
        seed.edit_transaction('personal', original, reviewed=True)
        snapshot = seed.export_budget('personal')
    finally:
        seed.close()
    snapshot.pop('history', None)
    original_row = next(row for row in snapshot['transactions'] if row['id'] == original)
    original_row.update(source='ynab', merchant='  Fictional receipt  ')
    plugin = load_plugins((storage,)).storage_plugin(storage)
    backend = open_storage(plugin, StorageContext(home, 'sandbox', {}))
    try:
        backend.restore_budget('personal', snapshot)
    finally:
        backend.close()
    executable = str(Path(sys.executable).parent / 'whab')

    def run(*args, ok=True, stdin=None):
        completed = subprocess.run([executable, *args, '--home', str(home), '--environment', 'sandbox'], env=env, input=stdin, capture_output=True, text=True, timeout=60)
        assert completed.returncode == (0 if ok else 1), completed.stderr
        return json.loads(completed.stdout if ok else completed.stderr)

    def query(operation):
        return run('api', '--budget', 'default', 'query', operation)

    def transactions():
        return {row['id']: row for row in query('transactions')['data']}

    split = {'preserveBalances': True, 'edits': [{'transactionId': original, 'amount': -80000}], 'creates': [{'account': 'Fictional savings 🍏', 'date': '2024-05-15', 'amount': -20000, 'currency': 'USD', 'merchant': 'Fictional lender', 'category': 'Fictional finance / Principal', 'note': original_row['note']}]}
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        before = query('portable')['data']
        before_rows = transactions()
        preview = run('My budget', 'trnx', 'edit-many', '--file', '-', '--dry-run', '--agent', 'Codex', '--batch', 'fictional-split', stdin=json.dumps(split))
        assert query('portable')['data'] == before
        recovery = ('My budget', 'trnx', 'edit-many', '--file', '-', '--agent', 'Codex', '--batch', 'fictional-split', '--revision', preview['expectedRevision'], '--request-id', preview['requestId'])
        saved = run(*recovery, stdin=json.dumps(split))
        assert saved['data'] == preview['data']
        created_id = saved['data']['createdIds'][0]
        current = transactions()
        assert set(current) == {*before_rows, created_id}
        assert current[original]['amount'] == -80000 and current[created_id]['amount'] == -20000
        assert current[original]['amount'] + current[created_id]['amount'] == before_rows[original]['amount']
        assert current[payment] == before_rows[payment]
        for key in ('id', 'accountId', 'date', 'merchant', 'categoryId', 'note', 'source'):
            assert current[original][key] == before_rows[original][key]
        assert current[created_id]['accountId'] == account and current[created_id]['categoryId'] == component
        assert current[created_id]['source'] == 'manual' and current[created_id]['note'] == original_row['note']
        assert not current[original]['reviewed'] and not current[created_id]['reviewed']
        assert query('portable')['data']['accounts'] == before['accounts']
        assert run(*recovery, stdin=json.dumps(split))['replayed']
        entry = run('default', 'history', 'list')['data']['entries'][0]
        assert entry['operation'] == 'change_transactions' and entry['actor'] == 'Codex' and entry['batchId'] == 'fictional-split'
        assert entry['reversible']
        for action in ('undo', 'redo'):
            plan = run('default', 'history', action, entry['id'])
            run('default', 'history', action, entry['id'], '--apply', plan['data']['fingerprint'])
            actual = transactions()
            assert actual == (before_rows if action == 'undo' else current)
        stable = query('portable')['data']
        invalid = {**split, 'creates': [{**split['creates'][0], 'currency': 'EUR'}]}
        failure = run('default', 'trnx', 'edit-many', '--file', '-', stdin=json.dumps(invalid), ok=False)
        assert 'currency' in failure['error'].lower() and query('portable')['data'] == stable
        stale = run('default', 'trnx', 'edit', original, '--amount', '-1', '--revision', preview['revision'], ok=False)
        assert 'refused' in stale['error'] and query('portable')['data'] == stable
        made = run('default', 'trnx', 'create', '--account', account, '--date', '2026-11-01', '--amount', '1250', '--currency', 'USD', '--payee', 'Fictional cafe', '--category', category, '--memo', 'New manual memo', '--agent', 'Codex')
        manual_id = made['data']['createdIds'][0]
        edited = run('default', 'trnx', 'edit', manual_id, '--date', '2026-11-02', '--payee', 'Fictional café 東京', '--amount', '1500', '--account', 'Fictional savings 🍏', '--note', 'Exact replacement')
        assert edited['data']['changes'][0]['after']['id'] == manual_id
        assert edited['data']['changes'][0]['after']['amount'] == 1500
        # Annotation-only syntax still selects its original operation and output.
        annotation = run('default', 'trnx', 'edit-many', '--file', '-', stdin=json.dumps({'edits': [{'transactionId': manual_id, 'appendNote': 'kept'}]}))
        assert annotation['intent']['operation'] == 'edit_transactions'
        assert annotation['data']['changes'][0]['after']['note'] == 'Exact replacement\nkept'
        other = run('budget', 'create', 'Fictional isolated')['id']
        pristine = run('api', '--budget', other, 'query', 'portable')['data']
        run(other, 'trnx', 'edit', original, '--amount', '10', ok=False)
        assert run('api', '--budget', other, 'query', 'portable')['data'] == pristine

    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        replay = run(*recovery, stdin=json.dumps(split))
        assert replay['replayed'] and replay['data'] == saved['data']
        assert transactions()[created_id] == current[created_id]
        entries = run('default', 'history', 'list')['data']['entries']
        assert sum(row['requestId'] == saved['requestId'] for row in entries) == 1
