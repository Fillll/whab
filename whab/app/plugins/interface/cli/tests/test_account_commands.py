"""Classification resolves exact names, preserves scope, and never replays writes."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def account_transport(monkeypatch, tmp_path):
    rows = [
        {'id': 'a', 'name': '🍏 Fictional Card', 'type': 'other', 'subtype': None, 'onBudget': False, 'balanceUnknown': True},
        {'id': 'b', 'name': 'Семейный счёт', 'type': 'credit', 'subtype': 'credit_card', 'onBudget': True},
        {'id': 'c', 'name': 'Same', 'type': 'other', 'subtype': None, 'onBudget': False},
        {'id': 'd', 'name': 'same', 'type': 'other', 'subtype': None, 'onBudget': False},
    ]
    state = {'rows': rows, 'revision': 'r1', 'failure': None, 'reads': 0}
    calls = []
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200, json={'budgets': [{'id': 'budget-1', 'name': 'Fictional бюджет'}]})
        if request.method == 'POST':
            if state['failure'] == 'lost':
                raise httpx.ReadError('private transport data')
            if state['failure'] == 'stale':
                return httpx.Response(409, json={'error': 'conflict'})
            intent = json.loads(request.content)
            row = next(row for row in rows if row['id'] == intent['payload']['accountId'])
            row.update({k: v for k, v in intent['payload'].items() if k != 'accountId'})
            state['revision'] = 'r2'
            return httpx.Response(200, json={'budgetId': 'foreign' if state['failure'] == 'wrong-write' else 'budget-1', 'revision': 'r2', 'data': None})
        state['reads'] += 1
        revision = None if state['failure'] == 'invalid-read' else 'r3' if state['failure'] == 'changed' and state['reads'] > 1 else state['revision']
        result = {'budgetId': 'foreign' if state['failure'] == 'wrong-read' else 'budget-1', 'revision': revision, 'data': rows}
        if state['failure'] == 'changed-fields' and state['reads'] > 1:
            rows[0]['subtype'] = 'line_of_credit'
        return httpx.Response(200, json=result)
    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console, 'local_connection', connection)
    monkeypatch.setattr(console.Path, 'home', lambda: tmp_path)
    return state, calls


def run(*args):
    return console.main(['Fictional бюджет', 'account', *args])


def test_list_and_show_by_unicode_name_or_id(account_transport, capsys):
    assert run('list', '--unclassified') == 0
    assert {row['id'] for row in json.loads(capsys.readouterr().out)['data']} == {'a', 'c', 'd'}
    for name in ['b', ' семейный СЧЁТ ']:
        assert run('show', name) == 0
        row = json.loads(capsys.readouterr().out)['data']
        assert row['subtype'] == 'credit_card' and row['onBudget'] and not row['unclassified']
    assert console.main(['budget-1', 'account', 'show', 'a']) == 0
    assert json.loads(capsys.readouterr().out)['budgetId'] == 'budget-1'
    assert all(r.method == 'GET' for r in account_transport[1])


@pytest.mark.parametrize('scope,expected', [([], False), (['--on-budget'], True), (['--off-budget'], False)])
def test_imported_account_scope_is_explicit_or_preserved(account_transport, capsys, scope, expected):
    assert run('classify', '🍏 Fictional Card', '--type', 'credit', '--subtype', 'credit_card', *scope) == 0
    result = json.loads(capsys.readouterr().out)
    row = result['data']
    assert row['onBudget'] is expected and row['subtype'] == 'credit_card' and not row['unclassified'] and row['balanceUnknown']
    posts = [r for r in account_transport[1] if r.method == 'POST']
    assert len(posts) == 1
    intent = json.loads(posts[0].content)
    assert intent == {'operation': 'update_account', 'workspace': 'personal',
                      'payload': {'accountId': 'a', 'type': 'credit', 'onBudget': expected, 'subtype': 'credit_card'},
                      'expected_revision': 'r1', 'request_id': result['requestId']}
    assert result['revision'] == 'r2' and result['expectedRevision'] == 'r1'


def test_same_type_preserves_subtype_and_scope_and_intents_are_unique(account_transport, capsys):
    ids = []
    for _ in range(2):
        assert run('classify', 'b', '--type', 'credit') == 0
        result = json.loads(capsys.readouterr().out)
        assert result['data']['onBudget'] and result['data']['subtype'] == 'credit_card'
        ids.append(result['requestId'])
    assert ids[0] != ids[1]
    assert run('classify', 'b', '--type', 'investment', '--off-budget') == 0
    assert json.loads(capsys.readouterr().out)['data']['subtype'] is None


@pytest.mark.parametrize('name', ['Same', 'missing', '🍏 Fictional'])
def test_ambiguous_missing_or_fuzzy_name_never_writes(account_transport, capsys, name):
    assert run('classify', name, '--type', 'credit') == 1
    error = json.loads(capsys.readouterr().err)['error']
    assert 'Candidates' in error and '"id":' in error and '"name":' in error
    assert all(r.method == 'GET' for r in account_transport[1])


def test_id_precedes_name(account_transport, capsys):
    account_transport[0]['rows'][0]['name'] = 'b'
    assert run('show', 'b') == 0
    assert json.loads(capsys.readouterr().out)['data']['id'] == 'b'


@pytest.mark.parametrize('name,options', [('b', ['--type', 'investment']), ('a', ['--type', 'investment', '--on-budget']), ('a', ['--type', 'credit', '--subtype', 'checking'])])
def test_invalid_domain_combinations_do_not_write(account_transport, capsys, name, options):
    assert run('classify', name, *options) == 1
    assert ('Only cash' if options[1] == 'investment' else 'Invalid account subtype') in capsys.readouterr().err
    assert account_transport[0]['revision'] == 'r1'
    assert all(r.method == 'GET' for r in account_transport[1])


@pytest.mark.parametrize('failure', ['wrong-read', 'invalid-read', 'lost', 'stale', 'wrong-write', 'changed', 'changed-fields'])
def test_wrong_budget_stale_or_uncertain_mutation_never_replays(account_transport, capsys, failure):
    account_transport[0]['failure'] = failure
    assert run('classify', 'a', '--type', 'credit') == 1
    result = capsys.readouterr()
    assert not result.out and 'private transport data' not in result.err
    posts = [r for r in account_transport[1] if r.method == 'POST']
    if posts:
        assert len(posts) == 1
        intent = json.loads(posts[0].content)
        assert intent['request_id'] in result.err and 'r1' in result.err and 'do not rerun classify' in result.err
    else:
        assert 'invalid response' in result.err


@pytest.mark.parametrize('action', [[], ['list'], ['show'], ['classify']])
def test_detailed_help_is_offline(account_transport, capsys, action):
    with pytest.raises(SystemExit) as result:
        run(*action, '--help')
    assert result.value.code == 0 and not account_transport[1]
    output = capsys.readouterr().out
    assert 'Examples' in output and '🍏Example Card' in output
    if action == ['classify']:
        assert 'preserve the existing scope' in output and '401k' in output and 'uncertain' in output


@pytest.mark.parametrize('options', [[], ['--type', 'invalid'], ['--type', 'credit', '--on-budget', '--off-budget']])
def test_argument_errors_are_offline(account_transport, capsys, options):
    with pytest.raises(SystemExit) as result:
        run('classify', 'a', *options)
    assert result.value.code == 2 and not account_transport[1]
    capsys.readouterr()


def test_tables_escape_controls_and_explain_empty_list(account_transport, capsys):
    account_transport[0]['rows'][0]['name'] = 'Long\x1b' + 'x' * 100
    assert run('list', '--format', 'table') == 0
    text = capsys.readouterr().out
    assert '\x1b' not in text and '\\u001b' in text and '…' in text
    assert 'Scope' in text and 'Unclassified' in text and 'off-budget' in text
    assert run('show', 'b', '--format', 'table') == 0
    assert 'on-budget' in capsys.readouterr().out
    assert run('classify', 'a', '--type', 'credit', '--format', 'table') == 0
    assert 'credit' in capsys.readouterr().out
    account_transport[0]['rows'].clear()
    assert run('list', '--unclassified', '--format', 'table') == 0
    assert 'No matching accounts' in capsys.readouterr().out
