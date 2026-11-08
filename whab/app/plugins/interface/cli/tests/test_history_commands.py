"""CLI history is a read/preview workflow with one explicit retained write."""

import json
from contextlib import contextmanager

import httpx
import pytest
from whab_plugin_cli import console

HASH = 'a' * 64


@pytest.fixture
def history_cli(monkeypatch, tmp_path):
    calls, state = [], {'fail':False, 'hash':HASH}
    def respond(request):
        calls.append(request)
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200, json={'budgets':[{'id':'budget', 'name':'Fictional'}]})
        body = json.loads(request.content)
        if request.url.path.endswith('/commands'):
            if state['fail']:
                raise httpx.ReadError('private information')
            return httpx.Response(200, json={'budgetId':'budget', 'revision':'new', 'data':None})
        return httpx.Response(200, json={'budgetId':'budget', 'revision':'observed', 'data':{'fingerprint':state['hash'], 'changes':[]}})
    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(respond)) as client:
            yield client
    monkeypatch.setattr(console, 'local_connection', connection)
    monkeypatch.setattr(console.Path, 'home', lambda:tmp_path)
    return calls, state


@pytest.mark.parametrize('args', [['list'], ['list', '--limit','1','--before','previous'], ['show','batch'], ['export','baseline'], ['undo','batch'], ['redo','batch'], ['restore','baseline']])
def test_reads_and_previews_never_issue_financial_commands(history_cli, capsys, args):
    assert console.main(['Fictional','history',*args]) == 0
    capsys.readouterr()
    assert all(not r.url.path.endswith('/commands') for r in history_cli[0])


@pytest.mark.parametrize('args', [['undo','batch','--apply',HASH], ['redo','batch','--apply',HASH,'--agent','Codex'], ['snapshot'], ['snapshot','--apply',HASH,'--revision','old','--request-id','same'], ['restore','baseline','--apply',HASH,'--revision','old','--request-id','same']])
def test_explicit_apply_is_one_guarded_command(history_cli, capsys, args):
    assert console.main(['Fictional','history',*args]) == 0
    result = json.loads(capsys.readouterr().out)
    bodies = [json.loads(r.content) for r in history_cli[0] if r.url.path.endswith('/commands')]
    assert len(bodies) == 1 and bodies[0]['payload']['fingerprint'] == HASH
    assert result['fingerprint'] == HASH
    if '--agent' in args:
        assert bodies[0]['history'] == {'actor':'Codex','actorKind':'agent'}
    if '--revision' in args:
        assert bodies[0]['expected_revision'] == 'old' and bodies[0]['request_id'] == 'same'


def test_stale_preview_is_not_written(history_cli, capsys):
    assert console.main(['Fictional','history','undo','batch','--apply','wrong']) == 1
    assert 'Preview again' in capsys.readouterr().err
    assert not history_cli[0][-1].url.path.endswith('/commands')


def test_lost_response_retains_exact_intent_without_replaying(history_cli, capsys):
    history_cli[1]['fail'] = True
    assert console.main(['Fictional','history','undo','batch','--apply',HASH,'--request-id','retained']) == 1
    error = capsys.readouterr().err
    assert '--request-id retained' in error and '--revision observed' in error
    assert 'private information' not in error
    assert len([r for r in history_cli[0] if r.url.path.endswith('/commands')]) == 1


def test_category_batch_is_explicit(history_cli, monkeypatch):
    from types import SimpleNamespace

    from whab_plugin_cli.transaction_commands import execute_category
    calls = []
    def exchange(*args, **kwargs):
        calls.append(kwargs)
        return {'budgetId':'budget', 'revision':'revision', 'data':[]}
    execute_category(None, 'budget', '/local', SimpleNamespace(transaction_id='transaction', filter='categorize', clear=True, agent='Codex', needs_review=False, revision=None, request_id='intent', batch='october'), exchange, console.CommandError)
    assert calls[-1]['json']['history'] == {'batchId':'october'}
