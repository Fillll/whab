"""Offline help and guarded, name-addressed Income classification presentation."""

import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from whab_plugin_cli import console


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    state = {'revision': 'before', 'posts': [], 'failure': None, 'groups': [
        {'id': 'finance', 'name': 'Finance', 'children': [
            {'id': 'salary', 'name': '給与 🍏', 'group': 'Finance', 'parentId': 'finance', 'target': 0, 'currency': 'USD'},
            {'id': 'fees', 'name': 'Fees', 'group': 'Finance', 'parentId': 'finance', 'target': 0, 'currency': 'USD'}]}]}
    def respond(request):
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200, json={'budgets': [{'id': 'budget', 'name': 'Fictional'}]})
        if request.method == 'POST':
            body = json.loads(request.content)
            if body['operation'] == 'preview_category_classification':
                return httpx.Response(200, json={'budgetId': 'budget',
                    'revision': 'stale' if state['failure'] == 'preview-revision' else state['revision'],
                    'data': {'categoryId': 'wrong' if state['failure'] == 'preview-id' else 'salary',
                             'before': deepcopy(state['groups'][0]['children'][0]), 'after': {'kind': body['parameters']['kind']}, 'impacts': []}})
            state['posts'].append(body)
            if state['failure'] == 'lost':
                raise httpx.ReadError('private detail')
            if state['failure'] == 'refused':
                return httpx.Response(409, json={'detail': 'Stale fictional state'})
            if body['payload']['kind'] == 'income':
                state['groups'][0]['children'][0]['kind'] = 'income'
            else:
                state['groups'][0]['children'][0].pop('kind', None)
            state['revision'] = 'saved'
            if state['failure'] == 'mismatch':
                state['groups'][0]['children'][1]['name'] = 'Unrelated change'
            return httpx.Response(200, json={'budgetId': 'budget', 'revision': 'saved', 'data': None})
        return httpx.Response(200, json={'budgetId': 'budget', 'revision': state['revision'], 'data': deepcopy(state['groups'])})
    @contextmanager
    def connect(*_args, **_kwargs):
        with httpx.Client(base_url='https://localhost:5551', transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console, 'local_connection', connect)
    monkeypatch.setenv('WHAB_HOME', str(tmp_path / 'fictional'))
    return state


@pytest.mark.parametrize('kind', ['income', 'expense'])
def test_saved_type_and_table(endpoint, capsys, kind):
    if kind == 'expense':
        endpoint['groups'][0]['children'][0]['kind'] = 'income'
    assert console.main(['Fictional', 'category', 'classify', 'Finance / 給与 🍏', '--type', kind, '--format', 'table', '--agent', 'Codex']) == 0
    assert kind in capsys.readouterr().out
    assert len(endpoint['posts']) == 1
    assert endpoint['posts'][0]['payload'] == {'categoryId': 'salary', 'kind': kind}
    assert endpoint['posts'][0]['expected_revision'] == 'before'


@pytest.mark.parametrize('output', ['json', 'table'])
def test_dry_run_never_writes(endpoint, capsys, output):
    assert console.main(['Fictional', 'category', 'classify', 'salary', '--type', 'income', '--dry-run', '--format', output]) == 0
    assert ('impacts' in capsys.readouterr().out) and endpoint['posts'] == []
    assert endpoint['revision'] == 'before'


@pytest.mark.parametrize('failure', ['preview-revision', 'preview-id', 'mismatch', 'refused', 'lost'])
def test_conflicts_and_uncertainty_do_not_retry(endpoint, capsys, failure):
    endpoint['failure'] = failure
    assert console.main(['Fictional', 'category', 'classify', 'salary', '--type', 'income']) == 1
    result = capsys.readouterr().err
    if failure.startswith('preview'):
        assert not endpoint['posts'] and 'changed during preview' in result
    else:
        assert len(endpoint['posts']) == 1 and 'No automatic retry' in result
        assert 'private detail' not in result


def test_classification_help_is_offline_and_complete(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv('WHAB_HOME', str(tmp_path / 'nonexistent'))
    with pytest.raises(SystemExit) as stopped:
        console.main(['Fictional', 'category', 'classify', '--help'])
    assert stopped.value.code == 0
    text = capsys.readouterr().out
    for expected in ['--type', '--dry-run', 'monthly assignments', 'Review', 'unclassifiedIncome', '--request-id', 'trnx categorize']:
        assert expected.casefold() in text.casefold()
