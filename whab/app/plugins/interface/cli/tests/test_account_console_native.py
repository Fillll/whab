"""Real console entry point classifies a fictional import through verified HTTPS."""

import json
import os
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

from whab_plugin_cli import console


def test_installed_console_classification_preserves_import_and_isolation(tmp_path, monkeypatch, capsys):
    home = tmp_path / 'runtime'
    monkeypatch.setenv('WHAB_HOME', str(home))
    monkeypatch.setenv('WHAB_UI', '')
    monkeypatch.setenv('WHAB_PLUGINS', '')
    monkeypatch.setenv('WHAB_STORAGE', 'sqlite')
    monkeypatch.setenv('ALLOW_LIVE_PLAID', 'false')
    monkeypatch.setattr(console.Path, 'home', lambda: tmp_path)
    executable = Path(sys.executable).parent / 'whab'
    env = {**os.environ, 'HOME': str(tmp_path)}

    def cli(*args, ok=True):
        result = subprocess.run([str(executable), *args], env=env, capture_output=True, text=True, timeout=40)
        assert result.returncode == (0 if ok else 1), result.stderr
        return json.loads(result.stdout if ok else result.stderr)

    def run(*args, ok=True):
        return cli('--environment', 'sandbox', *args, ok=ok)

    budget = run('budget', 'create', 'Fictional семья')['id']
    other = run('budget', 'create', 'Fictional other')['id']
    run(budget, 'plugin', 'enable', 'ynab-zip-importer')
    archive = tmp_path / 'fictional.zip'
    with ZipFile(archive, 'w') as export:
        export.writestr('Budget.csv', 'Month,Category Group/Category,Category Group,Category,Assigned,Activity,Available\nOct 2026,Living: Food,Living,Food,$100.00,-$12.34,$87.66\n')
        export.writestr('Register.csv', 'Account,Flag,Date,Payee,Category Group/Category,Category Group,Category,Memo,Outflow,Inflow,Cleared\n🍏 Fictional карта,,2026-10-01,Fictional shop,Living: Food,Living,Food,Keep this note,$12.34,$0.00,Cleared\n')
    preview = run(budget, 'import', 'ynab', 'preview', str(archive), '--currency', 'USD')
    run(budget, 'import', 'ynab', 'commit', str(archive), '--currency', 'USD', '--fingerprint', preview['fingerprint'])
    before = run('Fictional семья', 'account', 'list', '--unclassified')['data'][0]
    assert before['type'] == 'other' and not before['onBudget'] and before['balanceUnknown']
    transactions = run('api', '--budget', budget, 'query', 'transactions')['data']
    categories = run(budget, 'trnx', 'categories')['data']
    history = run(budget, 'history', 'list')['data']['entries']
    # Global options work at different positions in the actual installed script.
    saved = cli('Fictional семья', 'account', '--environment', 'sandbox', 'classify', before['id'], '--type', 'credit', '--home', str(home))
    assert saved['data']['onBudget'] is False
    profile = tmp_path / 'fixture.budget'
    profile.write_text(json.dumps({'home': str(home), 'environment': 'sandbox'}))
    saved = cli('--profile', str(profile), budget, 'account', 'classify', '🍏 Fictional карта', '--type', 'credit', '--on-budget', '--subtype', 'credit_card', '--ca', str(home / 'tls/ca.crt'))
    assert saved['data']['onBudget'] is True
    assert cli('--profile', str(profile), budget, 'account', 'show', before['id'])['data'] == saved['data']
    assert run(budget, 'account', 'list', '--unclassified')['data'] == []
    assert run(other, 'account', 'list')['data'] == []
    assert run('api', '--budget', budget, 'query', 'transactions')['data'] == transactions
    assert run(budget, 'trnx', 'categories')['data'] == categories
    assert run(budget, 'history', 'list')['data']['entries'][-len(history):] == history
    for key, value in before.items():
        if key not in {'type', 'subtype', 'onBudget', 'unclassified'}:
            assert saved['data'][key] == value
    revision = run(budget, 'account', 'show', before['id'])['revision']
    rejected = run(budget, 'account', 'classify', before['id'], '--type', 'investment', ok=False)
    assert 'Only cash' in rejected['error']
    assert run(budget, 'account', 'show', before['id'])['revision'] == revision

    # A real concurrent public write between read and classification is refused.
    exchange = console.exchange
    def concurrent(connection, method, path, **kwargs):
        if method == 'POST' and kwargs.get('json', {}).get('operation') == 'update_account':
            exchange(connection, method, path, json={**kwargs['json'], 'request_id': 'fictional-concurrent',
                'operation': 'add_account', 'payload': {'name': 'Concurrent fixture', 'type': 'other', 'balance': 0}})
        return exchange(connection, method, path, **kwargs)
    monkeypatch.setattr(console, 'exchange', concurrent)
    assert console.main(['--environment', 'sandbox', budget, 'account', 'classify', before['id'], '--type', 'credit', '--off-budget']) == 1
    assert 'not confirmed' in capsys.readouterr().err
    assert run(budget, 'account', 'show', before['id'])['data'] == saved['data']
