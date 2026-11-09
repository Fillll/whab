"""Both CLI entry points construct the same explicit scoped bank intents."""

import json

import pytest
from whab_plugin_cli import client, console
from whab_plugin_cli.bank_commands import bank_request


@pytest.mark.parametrize('entry', ['advanced', 'console'])
@pytest.mark.parametrize('arguments,method,suffix,payload', [
    (['connections', 'plaid'], 'GET', '/items', None),
    (['shared', 'plaid'], 'GET', '/connections', None),
    (['attach', 'plaid', '--item', 'item'], 'POST', '/attachments', {'itemId': 'item'}),
    (['detach', 'plaid', '--item', 'item'], 'DELETE', '/attachments/item', {}),
    (['disconnect', 'plaid', '--item', 'item', '--affected-budgets', '["a","b"]'], 'DELETE', '/items/item', {'budgetIds': ['a', 'b']}),
    (['preview', 'plaid', '--item', 'fictional-item'], 'POST', '/sync-preview', {'itemId': 'fictional-item'}),
    (['configure', 'plaid', '--item', 'fictional-item', '--accounts', '[{"bankAccountId":"remote","accountId":"local","since":"2026-10-01"}]'], 'PUT', '/sync-settings', {'itemId': 'fictional-item', 'accounts': [{'bankAccountId': 'remote', 'accountId': 'local', 'since': '2026-10-01'}]}),
    (['sync', 'plaid'], 'POST', '/sync', {'itemId': None}),
    (['schedule', 'plaid'], 'GET', '/schedule', None),
    (['schedule', 'plaid', '--every-hours', '3'], 'PUT', '/schedule', {'interval_seconds': 10800}),
    (['schedule', 'plaid', '--manual'], 'PUT', '/schedule', {'interval_seconds': None}),
    (['background', 'plaid', '--enable'], 'POST', '/schedule/background', {'enabled': True}),
    (['background', 'plaid', '--disable'], 'POST', '/schedule/background', {'enabled': False}),
])
def test_bank_intents_match_between_consoles(entry, arguments, method, suffix, payload):
    if entry == 'advanced':
        args = client.parser().parse_args(['bank', *arguments])
    else:
        args = console.parser(console.options_parser()).parse_args(['BUDGET', 'Fictional', 'bank', *arguments])
    actual_method, path, kwargs = bank_request(args, 'fictional-budget')
    assert actual_method == method and path == '/budgets/fictional-budget/api/banks/plaid' + suffix
    assert kwargs == ({} if payload is None else {'json': payload})


@pytest.mark.parametrize('arguments', [
    ['preview', 'plaid'], ['configure', 'plaid', '--item', 'item'],
    ['configure', 'plaid', '--item', 'item', '--accounts', '{}'],
    ['configure', 'plaid', '--item', 'item', '--accounts', 'invalid'],
    ['configure', 'plaid', '--item', 'item', '--accounts', 'x' * (128 * 1024 + 1)],
    ['background', 'plaid'], ['attach', 'plaid'], ['detach', 'plaid'], ['disconnect', 'plaid'],
    ['disconnect', 'plaid', '--item', 'item'],
    *[['disconnect', 'plaid', '--item', 'item', '--affected-budgets', value]
      for value in ['{}', '[1]', '["b","a"]', '["a","a"]', 'invalid', 'x' * (128 * 1024 + 1)]],
])
def test_incomplete_bank_intents_are_rejected_before_network(arguments):
    args = client.parser().parse_args(['bank', *arguments])
    with pytest.raises(ValueError):
        bank_request(args, 'fictional')


def test_both_console_entry_points_execute_bank_operation(monkeypatch, tmp_path, capsys):
    from contextlib import contextmanager
    from types import SimpleNamespace
    class Response:
        status_code = 200
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def iter_bytes(self): yield b'{"items":[]}'
    class Connection:
        base_url = 'https://localhost:5173'
        def stream(self, method, path, **kwargs):
            assert method == 'GET' and path.endswith('/api/banks/plaid/items')
            return Response()
    @contextmanager
    def local(*args, **kwargs): yield Connection()
    monkeypatch.setattr('whab.plugins.local.local_connection', local)
    assert client.main(['--home', str(tmp_path), 'bank', 'connections', 'plaid']) == 0
    assert json.loads(capsys.readouterr().out) == {'items': []}
    monkeypatch.setattr(console, 'local_connection', local)
    monkeypatch.setattr(console, 'resolve_budget', lambda *args: 'fictional')
    monkeypatch.setattr(console, 'exchange', lambda connection, method, path, **kwargs: {'items': []})
    args = SimpleNamespace(scope='BUDGET', command='bank', target='Fictional', action='connections', plugin='plaid')
    assert console.execute(args, str(tmp_path), 'sandbox', None) == {'items': []}


@pytest.mark.parametrize('raw,expected', [
    (b'{"error":"Choose a same-currency account."}', 'Choose a same-currency account.'),
    (b'not json', None), (b'\xff', None), (b'[]', None), (b'{"error":true}', None),
    (b'{"error":""}', None), (json.dumps({'error': 'x' * 1001}).encode(), None),
])
def test_bank_validation_errors_are_bounded_authored_host_messages(raw, expected):
    from whab_plugin_cli.bank_commands import bank_error
    assert bank_error(raw) == expected


@pytest.mark.parametrize('raw,expected', [(b'{"error":"Choose a same-currency account."}', 'same-currency'), (b'{}', 'failed')])
def test_bank_errors_are_actionable_in_both_cli_entry_points(monkeypatch, tmp_path, capsys, raw, expected):
    from contextlib import contextmanager
    class Response:
        status_code = 400
        def __enter__(self): return self
        def __exit__(self, *args): return None
        def iter_bytes(self): yield raw
    class Connection:
        base_url = 'https://localhost:5173'
        def stream(self, *args, **kwargs): return Response()
    @contextmanager
    def local(*args, **kwargs): yield Connection()
    monkeypatch.setattr('whab.plugins.local.local_connection', local)
    assert client.main(['--home', str(tmp_path), 'bank', 'connections', 'plaid']) == 1
    assert expected in capsys.readouterr().err
    monkeypatch.setattr(console, 'local_connection', local)
    monkeypatch.setattr(console, 'resolve_budget', lambda *args: 'fictional')
    assert console.main(['--home', str(tmp_path), 'Fictional', 'bank', 'connections', 'plaid']) == 1
    error = capsys.readouterr().err
    assert ('same-currency' if expected == 'same-currency' else 'HTTP 400') in error


@pytest.mark.parametrize('entry', ['advanced', 'simple'])
@pytest.mark.parametrize('options,expected', [
    ([], {'itemId': 'item', 'bankAccountId': None, 'startDate': None, 'endDate': None}),
    (['--bank-account', 'account', '--since', '2025-01-01', '--until', '2026-11-07'],
     {'itemId': 'item', 'bankAccountId': 'account', 'startDate': '2025-01-01', 'endDate': '2026-11-07'}),
])
def test_investment_intents_have_identical_scope_in_both_consoles(entry, options, expected):
    arguments = ['investments', 'plaid', '--item', 'item', *options]
    args = client.parser().parse_args(['bank', *arguments]) if entry == 'advanced' else console.parser(console.options_parser()).parse_args(['BUDGET', 'Fictional', 'bank', *arguments])
    assert bank_request(args, 'fictional') == ('POST', '/budgets/fictional/api/banks/plaid/investments', {'json': expected})


def test_investment_read_requires_an_explicit_connection_before_network():
    args = client.parser().parse_args(['bank', 'investments', 'plaid'])
    with pytest.raises(ValueError, match='connection ID'):
        bank_request(args, 'fictional')


@pytest.mark.parametrize('entry', ['advanced', 'console'])
def test_profile_save_reads_private_file_without_putting_credentials_in_arguments(entry, tmp_path):
    path = tmp_path / 'private.json'
    path.write_text('{"PLAID_CLIENT_ID":"fictional-client","PLAID_SECRET":"fictional-secret"}')
    argv = ['profile-save', 'plaid', '--profile', 'second-account', '--credentials-file', str(path)]
    args = client.parser().parse_args(['bank', *argv]) if entry == 'advanced' else console.parser(console.options_parser()).parse_args(['BUDGET', 'Fictional', 'bank', *argv])
    method, route, kwargs = bank_request(args, 'fictional-budget')
    assert method == 'PUT' and route.endswith('/profiles')
    assert kwargs['json'] == {'profileId': 'second-account', 'config': {'PLAID_CLIENT_ID': 'fictional-client', 'PLAID_SECRET': 'fictional-secret'}}
    assert 'fictional-secret' not in str(argv)


@pytest.mark.parametrize('content', ['invalid', '[]', '{"key":1}', 'x' * (128 * 1024 + 1)])
def test_profile_save_refuses_invalid_or_overlarge_private_input(tmp_path, content):
    path = tmp_path / 'private.json'
    path.write_text(content)
    args = client.parser().parse_args(['bank', 'profile-save', 'plaid', '--profile', 'second', '--credentials-file', str(path)])
    with pytest.raises(ValueError, match='bounded JSON'):
        bank_request(args, 'fictional')


def test_profile_save_stdin_and_missing_inputs(monkeypatch, tmp_path):
    import io
    from types import SimpleNamespace
    monkeypatch.setattr('whab_plugin_cli.bank_commands.sys.stdin', SimpleNamespace(buffer=io.BytesIO(b'{"PLAID_CLIENT_ID":"fictional","PLAID_SECRET":"fictional"}')))
    args = client.parser().parse_args(['bank', 'profile-save', 'plaid', '--profile', 'second', '--credentials-file', '-'])
    assert bank_request(args, 'fictional')[2]['json']['profileId'] == 'second'
    for argv in [[], ['--profile', 'second'], ['--credentials-file', '-'], ['--profile', 'second', '--credentials-file', str(tmp_path / 'missing')]]:
        args = client.parser().parse_args(['bank', 'profile-save', 'plaid', *argv])
        with pytest.raises(ValueError):
            bank_request(args, 'fictional')


def test_profile_list_reads_without_credentials():
    args = client.parser().parse_args(['bank', 'profiles', 'plaid'])
    assert bank_request(args, 'fictional') == ('GET', '/budgets/fictional/api/banks/plaid/profiles', {})
