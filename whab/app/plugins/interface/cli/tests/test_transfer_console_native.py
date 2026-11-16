"""Actual console arguments, HTTPS host, worker storage and restart replay."""

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


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
@pytest.mark.parametrize('zero', [False, True], ids=['nonzero', 'zero'])
def test_real_transfer_workflow_uses_fictional_home_and_exact_restart_retry(tmp_path, kind, zero):
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
            ids.append(store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': 'Fictional', 'amount': 0 if zero else amount, 'categoryId': None, 'note': '[YNAB]\nExact note  '}))
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
        assert pairs['data']['total'] == (0 if zero else 1)
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


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_real_atomic_transfer_file_stdin_and_saved_receipt(tmp_path, kind):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '', 'WHAB_PLUGINS': '',
           'WHAB_ENV': 'sandbox', 'WHAB_STORAGE': kind, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    from whab_plugin_sqlite import Store
    store = Store(':memory:')
    pairs, originals = [], {}
    try:
        accounts = [store.add_account('personal', {'name': name, 'type': 'depository', 'balance': 100000})
                    for name in ['旅行 🍏', 'Сбережения']]
        for index in range(3):
            ids = [store.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
                'merchant': '  Fictional transfer  ', 'amount': (0 if index == 1 else (index + 1) * 12345) * sign,
                'categoryId': None, 'note': '[YNAB]\nExact note  '})
                for account, sign in zip(accounts, [1, -1], strict=True)]
            pairs.append({'outId': ids[0], 'inId': ids[1]})
        snapshot = store.export_budget('personal')
        for row in snapshot['transactions']:
            row['merchant'] = '  Fictional transfer  '
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        originals = {row['id']: row for row in snapshot['transactions']}
    finally:
        store.close()
    backend = open_storage(load_plugins((kind,)).storage_plugin(kind), StorageContext(home, 'sandbox', {}))
    try:
        backend.restore_budget('personal', snapshot)
    finally:
        backend.close()
    executable = Path(sys.executable).parent / 'whab'
    def run(*parts, stdin=None, ok=True):
        result = subprocess.run([str(executable), '--home', str(home), '--environment', 'sandbox', *parts],
                                input=stdin, env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)
    path = tmp_path / 'pairs.json'
    path.write_text(json.dumps(pairs[:2]), encoding='utf-8')
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run('default', 'trnx', 'transfers')['data']['total'] == 2  # No automatic zero proposal.
        preview = run('My budget', 'trnx', 'match-transfers', '--file', str(path), '--reviewed', '--dry-run')
        assert preview['data']['count'] == 4
        before_history = run('default', 'history', 'list')['data']['entries']
        bad = run('default', 'trnx', 'match-transfers', '--file', '-', stdin=json.dumps([pairs[2], pairs[2]]), ok=False)
        assert 'disjoint' in bad['error']
        assert run('default', 'trnx', 'list')['revision'] == preview['revision']
        saved = run('default', 'trnx', 'match-transfers', '--file', str(path), '--reviewed', '--agent', 'Codex',
                    '--batch', 'fictional-pairs', '--revision', preview['revision'], '--fingerprint', preview['data']['fingerprint'])
        assert saved['data'] == preview['data']
        zero_changes = [row for row in saved['data']['changes'] if row['after']['amount'] == 0]
        assert len(zero_changes) == 2 and all(row['after']['reviewed'] for row in zero_changes)
        retry = ('default', 'trnx', 'match-transfers', '--file', str(path), '--reviewed', '--agent', 'Codex',
                 '--batch', 'fictional-pairs', '--revision', saved['expectedRevision'], '--fingerprint', saved['fingerprint'],
                 '--request-id', saved['requestId'])
        assert run(*retry)['data'] == saved['data']
        after_history = run('default', 'history', 'list')['data']['entries']
        assert len(after_history) == len(before_history) + 1
        assert sum(row['requestId'] == saved['requestId'] for row in after_history) == 1
        result = run('default', 'trnx', 'match-transfers', '--file', '-', stdin=json.dumps(pairs[2:]))
        assert result['data']['count'] == 2 and result['data']['reviewed'] is False
        rows = run('default', 'trnx', 'list')['data']
        for row in rows:
            original = originals[row['id']]
            for field in ('amount', 'date', 'merchant', 'note', 'accountId', 'categoryId'):
                assert row[field] == original[field]
            assert row['transferPeerId']
        assert run('default', 'trnx', 'transfer-audit')['data']['total'] == 0
        isolated = run('budget', 'create', 'Fictional isolated')['id']
        assert 'missing' in run(isolated, 'trnx', 'match-transfers', '--file', str(path), ok=False)['error'].lower()
    endpoint = home / 'run/sandbox/host.json'
    deadline = monotonic() + 25
    while endpoint.exists() and monotonic() < deadline:
        sleep(.05)
    assert not endpoint.exists()
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        assert run(*retry)['data'] == saved['data']
        history = run('default', 'history', 'list')['data']['entries']
        assert sum(row['requestId'] == saved['requestId'] for row in history) == 1


@pytest.mark.parametrize('kind', ['sqlite', 'json'])
def test_real_transfer_audit_and_guarded_legacy_repair(tmp_path, kind):
    home = tmp_path / 'runtime'
    env = {**os.environ, 'HOME': str(tmp_path), 'WHAB_HOME': str(home), 'WHAB_UI': '', 'WHAB_PLUGINS': '',
           'WHAB_ENV': 'sandbox', 'WHAB_STORAGE': kind, 'ALLOW_LIVE_PLAID': 'false'}
    env.pop('WHAB_BUDGET', None)
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
    store = open_storage(load_plugins((kind,)).storage_plugin(kind), StorageContext(home, 'sandbox', {}))
    try:
        snapshot = store.export_budget('personal')
        snapshot['accounts'] = [{'id': 'fictional-account', 'name': 'Fictional 🍏', 'type': 'depository', 'balance': 100000,
            'currency': 'USD', 'source': 'manual', 'institution': 'Fictional', 'onBudget': True, 'stale': False, 'balanceUnknown': False}]
        snapshot['categories'] = [{'id': 'legacy-transfer', 'name': 'Transfers', 'group': 'Finance', 'kind': 'transfer', 'target': 0, 'currency': 'USD'}]
        snapshot['transactions'] = [{'id': 'legacy-zero', 'accountId': 'fictional-account', 'date': '2026-11-01',
            'merchant': '  Fictional imported zero  ', 'merchantLogo': None, 'amount': 0, 'categoryId': 'legacy-transfer', 'pending': False,
            'reviewed': True, 'source': 'ynab', 'note': '[YNAB] exact  ', 'isTransfer': True, 'inferredTransfer': True,
            'categoryEdited': False, 'deleted': False}]
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        store.restore_budget('personal', snapshot)
    finally:
        store.close()
    executable = Path(sys.executable).parent / 'whab'
    def run(*parts):
        result = subprocess.run([str(executable), '--home', str(home), '--environment', 'sandbox', *parts],
                                env=env, capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    with patch.dict(os.environ, env, clear=True), local_connection(home, 'sandbox'):
        audit = run('My budget', 'trnx', 'transfer-audit', '--limit', '1')
        assert audit['data']['total'] == 1 and audit['data']['next'] is None
        assert audit['data']['issues'][0]['id'] == 'legacy-zero'
        preview = run('default', 'trnx', 'repair-transfers', 'legacy-zero', '--dry-run')
        after = preview['data']['changes'][0]['after']
        assert after['categoryId'] is None and after['reviewed'] is False and after['inferredTransfer'] is True
        saved = run('default', 'trnx', 'repair-transfers', 'legacy-zero', '--revision', preview['revision'],
                    '--fingerprint', preview['data']['fingerprint'], '--request-id', 'repair-fixture', '--agent', 'Codex')
        assert saved['data'] == preview['data']
        replay = run('default', 'trnx', 'repair-transfers', 'legacy-zero', '--revision', preview['revision'],
                     '--fingerprint', preview['data']['fingerprint'], '--request-id', 'repair-fixture', '--agent', 'Codex')
        assert replay['data'] == saved['data']
        row = run('default', 'trnx', 'list')['data'][0]
        assert row['amount'] == 0 and row['note'] == '[YNAB] exact  ' and row['merchant'] == '  Fictional imported zero  '
        assert not row['reviewed'] and row['categoryId'] is None
        issues = run('default', 'trnx', 'transfer-audit')['data']['issues']
        assert issues[0]['repairable'] is False
