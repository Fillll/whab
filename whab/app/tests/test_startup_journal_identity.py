"""Fictional offline lock recovery reaches the real HTTPS host and CLI again."""

import json
import os
import signal
import ssl
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path
from time import monotonic, sleep
from unittest.mock import patch

import httpx
import pytest

from whab.domain.history import financial
from whab.hosting import read_endpoint, runtime_directory
from whab.initialization import initialize_home
from whab.plugins import StorageContext, load_plugins
from whab.plugins.storage import open_storage

JOURNALS = {
    'visual-selection.json', 'namespace-effects.json', 'bank-continuations.json',
    'storage-intents.json', 'storage-upgrade.json', 'storage-writer.json', 'remote-client.json',
}


def environment(home, temporary):
    result = {key: os.environ[key] for key in ('PATH', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE') if key in os.environ}
    return {**result, 'HOME': str(temporary), 'WHAB_HOME': str(home), 'WHAB_ENV': 'sandbox',
            'WHAB_STORAGE': 'sqlite', 'WHAB_UI': 'web', 'WHAB_PLUGINS': '', 'ALLOW_LIVE_PLAID': 'false'}


def backend(home):
    return open_storage(load_plugins(('sqlite',)).storage_plugin('sqlite'), StorageContext(home, 'sandbox', {}))


def seed(home):
    store = backend(home)
    try:
        snapshot = store.export_budget('personal')
        snapshot['accounts'] = [
            {'id': identifier, 'name': name, 'institution': 'Fictional', 'type': 'depository',
             'balance': 123456, 'currency': 'USD', 'source': 'manual', 'onBudget': True,
             'balanceUnknown': False, 'stale': False}
            for identifier, name in [('cash', 'Fictional checking'), ('other', '旅行 account')]]
        snapshot['categories'] = [
            {'id': 'expense', 'name': 'Groceries', 'group': 'Fictional', 'kind': 'expense', 'target': 80000, 'currency': 'USD'}]
        snapshot['transactions'] = [
            {'id': 'purchase', 'accountId': 'cash', 'date': '2026-11-01', 'merchant': '  Fictional merchant  ',
             'merchantLogo': None, 'amount': 12345, 'categoryId': 'expense', 'pending': False,
             'reviewed': True, 'source': 'ynab', 'note': '[YNAB: cleared]\n  exact note ',
             'isTransfer': False, 'inferredTransfer': False, 'categoryEdited': False, 'deleted': False}]
        snapshot['history'] = {'formatVersion': 1, 'baseline': financial(snapshot), 'entries': []}
        store.restore_budget('personal', snapshot)
        return store.export_budget('personal')
    finally:
        store.close()


def app_command(home, env, *arguments, success=True):
    result = subprocess.run(
        [sys.executable, '-I', '-m', 'whab', '--home', str(home), '--environment', 'sandbox', *arguments],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert (result.returncode == 0) is success, result.stderr
    return result


@contextmanager
def running(home, env):
    process = subprocess.Popen(
        [sys.executable, '-I', '-m', 'whab', '--home', str(home), '--environment', 'sandbox', '--transient', '--serve'],
        env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
    )
    directory = runtime_directory(home, 'sandbox')
    try:
        deadline = monotonic() + 45
        while not (endpoint := read_endpoint(directory)):
            if process.poll() is not None:
                pytest.fail(process.communicate()[1])
            assert monotonic() < deadline, 'Fictional HTTPS host did not publish its endpoint.'
            sleep(.05)
        assert endpoint['pid'] == process.pid
        with httpx.Client(verify=ssl.create_default_context(cafile=endpoint['ca']), timeout=30) as client:
            deadline = monotonic() + 30
            while True:
                try:
                    page = client.get(endpoint['url'] + '/budgets/home/accounts')
                    break
                except httpx.ConnectError:
                    assert process.poll() is None and monotonic() < deadline
                    sleep(.05)
            assert page.status_code == 200 and 'Fictional checking' in page.text
        yield endpoint
    finally:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)
        assert read_endpoint(directory) is None


def cli(home, env, *arguments, success=True):
    executable = Path(sys.executable).parent / 'whab'
    command = [str(executable), '--home', str(home), '--environment', 'sandbox', *arguments]
    result = subprocess.run(command,
                            env=env, capture_output=True, text=True, timeout=45)
    assert (result.returncode == 0) is success, result.stderr
    return json.loads(result.stdout if success else result.stderr)


def test_explicit_previous_mount_recovery_preserves_ledger_and_reopens_cli_web(tmp_path):
    home = tmp_path / 'fictional runtime'
    env = environment(home, tmp_path)
    with patch.dict(os.environ, env, clear=True):
        initialize_home(home)
        before = seed(home)
    protected = {path: path.read_bytes() for path in [home / '.env', *list((home / 'tls').iterdir())]}
    with running(home, env):
        original_status = cli(home, env, 'My budget', 'plugin', 'status')
        accounts = cli(home, env, 'My budget', 'account', 'list')
        transactions = cli(home, env, 'My budget', 'trnx', 'list')
        history = cli(home, env, 'My budget', 'history', 'list')
        assert original_status['host_url'].startswith('https://localhost:')

    # Emulate the exact legacy state from a prior mount: same private lock token
    # and inode, old numerical device. The public recovery may change identity
    # metadata only, never the saved body, scope or revision.
    retained, original_bytes = {}, {}
    for path in home.rglob('*.json'):
        if path.name not in JOURNALS:
            continue
        original_bytes[path] = path.read_bytes()
        record = json.loads(original_bytes[path])
        lock = path.with_name(path.name + '.lock')
        info = lock.stat()
        record['format'] = 1
        record['lock'] = {'token': lock.read_text().strip(), 'device': str(info.st_dev + 1), 'inode': str(info.st_ino)}
        path.write_text(json.dumps(record))
        retained[path] = record
    assert any(path.name == 'visual-selection.json' for path in retained)
    raw_before = {path: path.read_bytes() for path in retained}
    refused = app_command(home, env, '--transient', '--serve', success=False)
    assert 'recover-journal-locks' in refused.stderr
    assert all(path.read_bytes() == raw for path, raw in raw_before.items())

    if sys.platform == 'darwin':
        preview = cli(home, env, 'runtime', 'recover-journal-locks')
        assert preview['count'] == len(retained) and len(preview['fingerprint']) == 64
        assert all(path.read_bytes() == raw for path, raw in raw_before.items())
        assert all(path.read_bytes() == raw for path, raw in protected.items())
        assert 'Fictional merchant' not in json.dumps(preview) and 'exact note' not in json.dumps(preview)
        assert json.loads(app_command(home, env, '--recover-journal-locks').stdout) == preview
        saved = cli(home, env, 'runtime', 'recover-journal-locks', '--apply', preview['fingerprint'])
        assert saved['status'] == 'complete' and saved['repaired'] == len(retained)
        repaired_bytes = {path: path.read_bytes() for path in retained}
        assert cli(home, env, 'runtime', 'recover-journal-locks', '--apply', preview['fingerprint']) == {**saved, 'repaired': 0}
        assert all(path.read_bytes() == raw for path, raw in repaired_bytes.items())
        for path, previous in retained.items():
            observed = json.loads(path.read_bytes())
            assert observed['format'] == 2 and observed['lock'] == json.loads(original_bytes[path])['lock']
            assert {key: value for key, value in observed.items() if key not in {'format', 'lock'}} == {
                key: value for key, value in previous.items() if key not in {'format', 'lock'}}
    else:
        # Linux retains device identity and cannot safely rebind this simulated
        # macOS remount. The public command must refuse without changing bytes.
        rejected = cli(home, env, 'runtime', 'recover-journal-locks', success=False)
        assert rejected['error'].startswith('unsupported:')
        assert 'unsupported:' in app_command(home, env, '--recover-journal-locks', success=False).stderr
        assert all(path.read_bytes() == raw for path, raw in raw_before.items())
        assert not (home / 'plugin-workers' / 'journal-recovery').exists()
        # Restore only our synthetic corruption; this is test-fixture cleanup,
        # not a product recovery strategy. Verify normal connectivity afterward.
        for path, raw in original_bytes.items():
            path.write_bytes(raw)

    for _ in range(2):
        with running(home, env):
            assert cli(home, env, 'My budget', 'plugin', 'status')['configured'] == original_status['configured']
            for group, previous in [('account', accounts), ('trnx', transactions), ('history', history)]:
                observed = cli(home, env, 'My budget', group, 'list')
                # The public concurrency guard is renewed on host startup;
                # persisted journal revisions and every saved field stay exact.
                assert observed['revision']
                assert {key: value for key, value in observed.items() if key != 'revision'} == {
                    key: value for key, value in previous.items() if key != 'revision'}
    with patch.dict(os.environ, env, clear=True):
        store = backend(home)
        try:
            assert store.export_budget('personal') == before
        finally:
            store.close()
    assert all(path.read_bytes() == raw for path, raw in protected.items())


@pytest.mark.parametrize('case', ['missing-home', 'missing-environment', 'serve', 'transient', 'budget', 'run-jobs', 'apply-alone'])
def test_offline_recovery_requires_explicit_scope_and_cannot_start_normal_work(tmp_path, case):
    home = tmp_path / 'must not be initialized'
    env = environment(home, tmp_path)
    arguments = ['--home', str(home), '--environment', 'sandbox', '--recover-journal-locks']
    if case == 'missing-home':
        arguments = arguments[2:]
    elif case == 'missing-environment':
        arguments = arguments[:2] + arguments[4:]
    elif case == 'budget':
        arguments += ['--budget', str(tmp_path / 'must-not-open.whab')]
    elif case == 'apply-alone':
        arguments = arguments[:-1] + ['--apply', 'a' * 64]
    else:
        arguments += ['--' + case]
    result = subprocess.run([sys.executable, '-I', '-m', 'whab', *arguments],
                            env=env, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'usage:' in result.stderr
    assert not home.exists() and not (tmp_path / 'must-not-open.whab').exists()
