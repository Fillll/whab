"""Manual register CLI previews, guarded creation, receipts and honest balance labels."""

import json
from contextlib import contextmanager
from copy import deepcopy

import httpx
import pytest
from whab_plugin_cli import console
from whab_plugin_cli.account_commands import account_table

from whab.plugins import StoreError, normalize_manual_account


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    state = {'rows': [], 'revision': 'before', 'posts': [], 'failure': None, 'receipts': {}, 'activity': 0}

    def respond(request):
        if request.url.path.endswith('/budgets'):
            return httpx.Response(200, json={'budgets': [{'id': 'fictional-id', 'name': 'Fictional бюджет'}]})
        if request.method == 'POST':
            original = json.loads(request.content)
            state['posts'].append(original)
            failure = state['failure']
            if failure == 'lost':
                raise httpx.ReadError('private transport credential')
            if failure in {400, 409, 503}:
                return httpx.Response(failure, json={'detail': 'Fictional refused or pending save'})
            request_id = original['request_id']
            if request_id in state['receipts']:
                previous, receipt = state['receipts'][request_id]
                if previous != original:
                    return httpx.Response(409, json={'detail': 'Request ID reused with different intent'})
                return httpx.Response(200, json=receipt)
            if original['expected_revision'] != state['revision']:
                return httpx.Response(409, json={'detail': 'Stale revision'})
            try:
                payload = normalize_manual_account(original['payload'], state['rows'])
            except StoreError as error:
                return httpx.Response(400, json={'detail': str(error)})
            payload.pop('uniqueName')
            state['rows'].append({**payload, 'id': 'created-account', 'institution': 'Manual account', 'source': 'manual', 'stale': False})
            state['revision'] = 'saved'
            receipt = {'budgetId': 'foreign' if failure == 'wrong-budget' else 'fictional-id', 'revision': 'saved',
                       'data': None if failure == 'invalid-result' else '' if failure == 'empty-id' else 'absent' if failure == 'missing-id' else 'created-account'}
            state['receipts'][request_id] = (deepcopy(original), receipt)
            return httpx.Response(200, json=receipt)
        data = deepcopy(state['rows'])
        for row in data:
            if state['failure'] != 'activity-missing':
                row['recordedActivity'] = state['activity']
        if state['posts'] and state['failure'] == 'different-account':
            data[-1]['balance'] = 42
        return httpx.Response(200, json={'budgetId': 'fictional-id',
            'revision': 'changed' if state['posts'] and state['failure'] == 'changed-revision' else state['revision'], 'data': data})

    @contextmanager
    def connect(*_args, **_kwargs):
        with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(console, 'local_connection', connect)
    monkeypatch.setenv('WHAB_HOME', str(tmp_path / 'fictional'))
    return state


def arguments(*extra, name=' Épargne 旅行 ', kind='depository', scope='--off-budget', currency='USD'):
    return ['Fictional бюджет', 'account', 'create', name, '--type', kind, scope, '--currency', currency, *extra]


def test_preview_then_create_retains_exact_intent_and_verified_identity(endpoint, capsys):
    options = ['--subtype', 'savings', '--agent', 'Codex', '--batch', 'fictional-import']
    assert console.main(arguments(*options, '--dry-run')) == 0
    preview = json.loads(capsys.readouterr().out)
    assert not endpoint['posts'] and not endpoint['rows'] and endpoint['revision'] == 'before'
    assert preview['dryRun'] and preview['data']['id'] is None
    assert preview['data']['name'] == 'Épargne 旅行' and preview['data']['currency'] == 'USD'
    assert preview['data']['balanceUnknown'] and preview['data']['recordedActivity'] == 0
    assert preview['intent']['history'] == {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-import'}
    assert console.main(arguments(*options, '--request-id', preview['requestId'], '--revision', preview['expectedRevision'])) == 0
    saved = json.loads(capsys.readouterr().out)
    assert saved['data']['id'] == 'created-account' and saved['savedRevision'] == saved['revision'] == 'saved'
    assert not saved['dryRun'] and not saved['replayed']
    assert saved['data']['balanceStatus'] == 'unknown'
    assert saved['data']['source'] == 'manual' and saved['data']['balance'] == 0
    assert endpoint['posts'] == [preview['intent']]
    for selection in ('created-account', 'épargne 旅行'):
        assert console.main(['fictional-id', 'account', 'show', selection]) == 0
        assert json.loads(capsys.readouterr().out)['data'] == saved['data']
    assert console.main(['fictional-id', 'account', 'list']) == 0
    assert json.loads(capsys.readouterr().out)['data'] == [saved['data']]


def test_exact_recovery_reuses_receipt_despite_duplicate_name_or_later_activity(endpoint, capsys):
    args = arguments('--request-id', 'intent', '--revision', 'before')
    assert console.main(args) == 0
    saved = json.loads(capsys.readouterr().out)
    endpoint['rows'][0]['balance'] = -12345
    endpoint['activity'] = -12345
    endpoint['revision'] = 'later'
    for fmt in ('json', 'table'):
        assert console.main([*args, '--format', fmt]) == 0
        output = capsys.readouterr().out
        if fmt == 'json':
            retried = json.loads(output)
            assert retried['data']['id'] == saved['data']['id'] and retried['replayed']
            assert retried['data']['recordedActivity'] == -12345
            assert retried['revision'] == 'later' and retried['savedRevision'] == 'saved'
        else:
            assert 'Exact request replay; current account shown' in output and '-12345 USD minor units' in output
    assert len(endpoint['rows']) == 1 and endpoint['posts'][0] == endpoint['posts'][1] == endpoint['posts'][2]
    assert console.main(arguments('--request-id', 'intent', '--revision', 'before', name='Different')) == 1
    assert 'Request ID reused with different intent' in capsys.readouterr().err
    assert len(endpoint['rows']) == 1


def test_manual_credit_activity_presentation_and_preview_table(endpoint, capsys):
    assert console.main(arguments('--dry-run', '--format', 'table', kind='credit', scope='--on-budget')) == 0
    output = capsys.readouterr().out
    assert '(assigned on save)' in output and 'Preview only; nothing saved' in output
    assert 'UNKNOWN' in output and '+0 USD minor units' in output and '"expected_revision": "before"' in output
    assert console.main(arguments('--format', 'table', kind='credit', scope='--on-budget')) == 0
    assert 'Saved account identity verified.' in capsys.readouterr().out
    endpoint['rows'][0]['balance'] = 4500
    endpoint['activity'] = -4500
    assert console.main(['fictional-id', 'account', 'show', 'created-account', '--format', 'table']) == 0
    assert '-4500 USD minor units' in capsys.readouterr().out
    endpoint['failure'] = 'activity-missing'
    assert console.main(['fictional-id', 'account', 'show', 'created-account', '--format', 'table']) == 0
    assert 'recorded activity unavailable' in capsys.readouterr().out
    endpoint['rows'][0]['balanceUnknown'] = False
    assert console.main(['fictional-id', 'account', 'show', 'created-account']) == 0
    row = json.loads(capsys.readouterr().out)['data']
    assert 'recordedActivity' not in row
    assert 'UNKNOWN' not in account_table({'budget': 'Fictional', 'budgetId': 'fictional-id', 'data': row, 'dryRun': True})


@pytest.mark.parametrize('extra', [['--request-id', 'intent'], ['--revision', 'before']])
def test_recovery_requires_paired_guards_without_write(endpoint, capsys, extra):
    assert console.main(arguments(*extra)) == 1
    assert 'both --revision and --request-id' in capsys.readouterr().err
    assert not endpoint['posts']


@pytest.mark.parametrize('extra,kwargs', [
    (['--subtype', 'savings'], {'kind': 'deposit'}),
    ([], {'kind': 'investment', 'scope': '--on-budget'}),
    ([], {'currency': 'invalid'}), ([], {'name': '  '}),
])
def test_domain_validation_refuses_before_post(endpoint, capsys, extra, kwargs):
    assert console.main(arguments(*extra, **kwargs)) == 1
    assert json.loads(capsys.readouterr().err)['error']
    assert not endpoint['posts'] and not endpoint['rows']


def test_duplicate_names_are_actionable_and_dry_run_staleness_never_writes(endpoint, capsys):
    assert console.main(arguments()) == 0
    capsys.readouterr()
    for extra in ([], ['--dry-run', '--request-id', 'preview', '--revision', 'saved']):
        assert console.main(arguments(*extra, name=' éPARGNE 旅行 ')) == 1
        assert 'created-account' in capsys.readouterr().err
    assert console.main(arguments('--dry-run', '--request-id', 'preview', '--revision', 'before')) == 1
    assert 'preview revision is stale' in capsys.readouterr().err
    assert len(endpoint['posts']) == 1
    # An explicit fresh request still reaches the atomic duplicate-name guard.
    assert console.main(arguments('--request-id', 'new', '--revision', 'saved')) == 1
    assert 'already exists' in capsys.readouterr().err
    assert len(endpoint['rows']) == 1
    assert console.main(arguments('--request-id', 'stale', '--revision', 'before', name='New name')) == 1
    assert 'Stale revision' in capsys.readouterr().err
    assert len(endpoint['rows']) == 1


@pytest.mark.parametrize('failure', [400, 409, 503, 'lost', 'wrong-budget', 'invalid-result', 'empty-id', 'missing-id', 'different-account', 'changed-revision', 'activity-missing'])
def test_uncertainty_and_refusals_keep_original_intent_without_replay(endpoint, capsys, failure):
    endpoint['failure'] = failure
    assert console.main(arguments('--agent', 'Codex', '--batch', 'fictional')) == 1
    message = json.loads(capsys.readouterr().err)['error']
    assert 'No automatic retry' in message and 'exact original intent' in message
    assert 'may have been saved' in message
    assert ('Fictional refused or pending save' in message) == (failure in {400, 409})
    assert 'private transport credential' not in message
    assert len(endpoint['posts']) == 1
    assert endpoint['posts'][0]['payload']['uniqueName'] and endpoint['posts'][0]['payload']['balanceUnknown']
    assert endpoint['posts'][0]['expected_revision'] == 'before'


@pytest.mark.parametrize('args', [[], ['Fictional', 'account'], ['Fictional', 'account', 'create']])
def test_complete_offline_help(monkeypatch, capsys, args):
    def forbidden(*_args, **_kwargs):
        pytest.fail('Help must not contact the host')
    monkeypatch.setattr(console, 'local_connection', forbidden)
    with pytest.raises(SystemExit) as stopped:
        console.main([*args, '--help'])
    assert stopped.value.code == 0
    help_text = capsys.readouterr().out
    for text in ('account create', 'depository/savings', 'UNKNOWN', '--dry-run', '--request-id', '--revision', 'recordedActivity', 'No automatic retry'):
        assert text in help_text


@pytest.mark.parametrize('omitted', ['--type', '--currency', '--off-budget'])
def test_explicit_classification_currency_and_scope_are_required(endpoint, capsys, omitted):
    args = arguments()
    index = args.index(omitted)
    del args[index:index + (1 if omitted == '--off-budget' else 2)]
    with pytest.raises(SystemExit) as stopped:
        console.main(args)
    assert stopped.value.code == 2
    assert 'required' in capsys.readouterr().err
    assert not endpoint['posts']


def test_mutually_exclusive_scope_and_no_balance_or_bank_arguments(endpoint, capsys):
    for extra in (['--on-budget'], ['--balance', '0'], ['--bank', 'example']):
        with pytest.raises(SystemExit) as stopped:
            console.main(arguments(*extra))
        assert stopped.value.code == 2
        assert 'error:' in capsys.readouterr().err
    assert not endpoint['posts']
