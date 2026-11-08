"""Short category commands preserve identity, attribution and optimistic writes."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def setup(monkeypatch, tmp_path):
    calls = []
    state = {'wrong': False, 'fail': None, 'post_wrong': False}
    categories = [{'id': 'food', 'name': 'Food', 'group': 'Living'},
                  {'id': 'other-food', 'name': 'Food', 'group': 'Travel'},
                  {'id': 'books', 'name': 'Books', 'group': 'Fun'}]
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200, json={'budgets': [{'id': 'budget', 'name': 'Fictional'}]})
        if request.method == 'POST':
            if state['fail'] == 'lost':
                raise httpx.ReadError('private token')
            return httpx.Response(state['fail'] or 200, json={'budgetId': 'wrong' if state['post_wrong'] else 'budget', 'revision': 'next', 'data': None})
        return httpx.Response(200, json={'budgetId': 'wrong' if state['wrong'] else 'budget', 'revision': 'observed', 'data': categories})
    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console, 'local_connection', connection)
    monkeypatch.setattr(console.Path, 'home', lambda: tmp_path)
    return calls, state


def test_categories_are_read_only(setup, capsys):
    assert console.main(['Fictional', 'trnx', 'categories']) == 0
    assert len(json.loads(capsys.readouterr().out)['data']) == 3
    assert all(r.method == 'GET' for r in setup[0])


@pytest.mark.parametrize('category', ['books', 'Books', 'fun / books'])
def test_names_and_ids_assign_one_guarded_intent(setup, capsys, category):
    assert console.main(['Fictional', 'trnx', 'categorize', 'txn', '--category', category, '--agent', 'Codex', '--needs-review']) == 0
    result = json.loads(capsys.readouterr().out)
    request = setup[0][-1]
    payload = json.loads(request.content)
    assert request.url.path == '/budgets/budget/api/local/v1/commands'
    assert payload == {'operation': 'edit_transaction', 'workspace': 'personal', 'payload': {'transactionId': 'txn', 'categoryId': 'books', 'categoryAgent': 'Codex', 'reviewed': False}, 'expected_revision': 'observed', 'request_id': result['requestId']}
    assert result['expectedRevision'] == 'observed' and result['categoryId'] == 'books'


def test_manual_clear_alias_and_explicit_retry_identity(setup, capsys):
    assert console.main(['Fictional', 'transactions', 'categorise', 'txn', '--clear', '--revision', 'old', '--request-id', 'same']) == 0
    body = json.loads(setup[0][-1].content)
    assert body['payload'] == {'transactionId': 'txn', 'categoryId': None}
    assert body['request_id'] == 'same' and body['expected_revision'] == 'old'
    capsys.readouterr()


@pytest.mark.parametrize('category', ['Food', 'missing'])
def test_unknown_or_ambiguous_names_do_not_write(setup, capsys, category):
    assert console.main(['Fictional', 'trnx', 'categorize', 'txn', '--category', category]) == 1
    assert 'ambiguous category' in capsys.readouterr().err
    assert all(r.method == 'GET' for r in setup[0])


def test_wrong_read_budget_does_not_write(setup, capsys):
    setup[1]['wrong'] = True
    assert console.main(['Fictional', 'trnx', 'categories']) == 1
    assert 'another budget' in capsys.readouterr().err
    assert all(r.method == 'GET' for r in setup[0])


@pytest.mark.parametrize('failure', [409, 'lost', 'wrong'])
def test_failed_write_is_not_replayed_and_retains_intent(setup, capsys, failure):
    if failure == 'wrong':
        setup[1]['post_wrong'] = True
    else:
        setup[1]['fail'] = failure
    assert console.main(['Fictional', 'trnx', 'categorize', 'txn', '--category', 'books', '--request-id', 'retained']) == 1
    error = capsys.readouterr().err
    assert 'private token' not in error and '--request-id retained' in error and '--revision observed' in error
    assert sum(r.method == 'POST' for r in setup[0]) == 1


@pytest.mark.parametrize('args', [['--help'], ['txn'], ['txn', '--clear', '--category', 'books'], ['txn', '--category', 'books', '--agent', ' bad']])
def test_help_and_invalid_arguments_are_offline(setup, capsys, args):
    with pytest.raises(SystemExit):
        console.main(['Fictional', 'trnx', 'categorize', *args])
    assert not setup[0]
    capsys.readouterr()


def test_malformed_envelope_fails_closed():
    from whab_plugin_cli.transaction_commands import checked
    for result in [[], {'budgetId': 'budget', 'data': []}, {'budgetId': 'budget', 'revision': 'r'}]:
        with pytest.raises(console.CommandError):
            checked(result, 'budget', console.CommandError)
