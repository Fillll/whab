"""Both CLI entry points construct the same explicit scoped bank intents."""

import json

import pytest
from whab_plugin_cli import client, console
from whab_plugin_cli.bank_commands import bank_request


@pytest.mark.parametrize('entry', ['advanced', 'console'])
@pytest.mark.parametrize('arguments,method,suffix,payload', [
    (['connections', 'plaid'], 'GET', '/items', None),
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
    ['background', 'plaid'],
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
