"""Short transaction editing, name resolution and exact-recovery diagnostics."""

import io
import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from uuid import UUID

import httpx
import pytest
from whab_plugin_cli import bulk_commands, console
from whab_plugin_cli import transaction_changes as changes
from whab_plugin_sqlite import Store

from whab.plugins import LedgerService


@pytest.fixture
def endpoint(monkeypatch, tmp_path):
    store = Store(':memory:', month='2026-11')
    service = LedgerService(store, budget_id='fictional')
    account = service.add_account('personal', {'name': '旅行 🍏', 'type': 'depository', 'balance': 100000})
    category = service.add_category('personal', {'name': '食料品', 'group': '家族', 'target': 0})
    transaction = service.add_transaction('personal', {'accountId': account, 'date': '2026-11-02', 'merchant': 'Fictional shop', 'amount': -10000, 'categoryId': category, 'note': '  [YNAB] \nOriginal  '})
    service.edit_transaction('personal', transaction, reviewed=True)
    flags = {'posts': [], 'fault': None, 'accounts_revision': False, 'preview_revision': False}

    def exchange(connection, method, path, **kwargs):
        if path.endswith('/budgets'):
            return {'budgets': [{'id': 'fictional', 'name': 'Fictional'}]}
        if method == 'GET':
            result = service.query(path.rsplit('/', 1)[1], 'personal')
            if path.endswith('/accounts') and flags['accounts_revision']:
                result['revision'] = 'other'
        else:
            body = kwargs['json']
            if path.endswith('/queries'):
                result = service.query(body['operation'], 'personal', parameters=body['parameters'])
                if flags['preview_revision']:
                    result['revision'] = 'other'
            else:
                flags['posts'].append(deepcopy(body))
                if flags['fault'] == 'lost':
                    raise httpx.ReadError('private connection information')
                if flags['fault'] in (400, 409):
                    error = console.CommandError('Fictional refusal')
                    error.status_code = flags['fault']
                    raise error
                result = service.command(body['operation'], 'personal', body['payload'], request_id=body['request_id'], expected_revision=body['expected_revision'], history=body.get('history'))
                result = deepcopy(result)
                if flags['fault'] == 'different':
                    result['data']['changed'] = 99
                if flags['fault'] == 'count':
                    result['data']['count'] = 99
                if flags['fault'] == 'ids':
                    result['data']['changes'][0]['id'] = 'different'
                if flags['fault'] == 'createdIds':
                    result['data']['createdIds'] = ['different']
                if flags['fault'] == 'none':
                    result['data'] = None
                if flags['fault'] == 'missing':
                    result['data'].pop('changes')
                if flags['fault'] == 'foreign':
                    return {**result, 'budgetId': 'foreign'}
        return {**result, 'budgetId': 'fictional'}

    monkeypatch.setattr(console, 'local_connection', lambda *a, **kw: nullcontext(None))
    monkeypatch.setattr(console, 'exchange', exchange)
    yield flags, service, account, category, transaction, exchange
    store.close()


def options(**kw):
    return SimpleNamespace(target='Fictional', filter='edit-many', agent=None, batch=None, revision=None, request_id=None, dry_run=False, **kw)


def invoke(endpoint, args, edits, creates=(), **kwargs):
    return changes.execute_changes(None, 'fictional', '/fictional', args, endpoint[-1], console.CommandError, edits=edits, creates=list(creates), **kwargs)


def test_preview_exact_created_identity_and_saved_replay(endpoint):
    flags, service, account, category, transaction, _ = endpoint
    before = service.query('portable', 'personal')
    edits = [{'transactionId': transaction, 'amount': -7000}]
    creates = [{'account': '旅行 🍏', 'date': '2026-11-02', 'merchant': 'Fictional lender', 'amount': -3000, 'currency': 'USD', 'category': '家族 / 食料品', 'note': 'Original copied'}]
    args = options()
    args.agent, args.batch, args.dry_run = 'Codex', 'split', True
    preview = invoke(endpoint, args, edits, creates, preserve_balances=True)
    assert service.query('portable', 'personal') == before and not flags['posts']
    created_id = preview['data']['createdIds'][0]
    assert str(UUID(created_id)) == created_id
    assert preview['intent']['payload']['preserveBalances'] is True
    assert preview['data']['changes'][0]['after']['categoryId'] == category
    assert preview['data']['changes'][0]['after']['note'] == '  [YNAB] \nOriginal  '
    assert preview['data']['changes'][1]['before'] is None
    assert preview['data']['changes'][1]['after']['categoryAgent'] == 'Codex'
    assert not preview['data']['changes'][0]['after']['reviewed']
    assert all(row['before'] == row['after'] for row in preview['data']['balanceChanges'])
    table = bulk_commands.bulk_table(preview)
    assert 'new manual transaction' in table and 'Account balance' in table and preview['requestId'] in table
    args.dry_run, args.request_id, args.revision = False, preview['requestId'], preview['expectedRevision']
    saved = invoke(endpoint, args, edits, creates, preserve_balances=True)
    assert saved['data'] == preview['data'] and len(flags['posts']) == 1
    replayed = invoke(endpoint, args, edits, creates, preserve_balances=True)
    assert replayed['replayed'] and replayed['data'] == saved['data']
    assert flags['posts'][1] == flags['posts'][0]


def test_resolution_and_preview_fail_before_write(endpoint):
    flags, service, account, category, transaction, _ = endpoint
    args = options()
    flags['accounts_revision'] = True
    with pytest.raises(console.CommandError, match='account/category name resolution'):
        invoke(endpoint, args, [{'transactionId': transaction, 'amount': 1}])
    flags['accounts_revision'] = False
    for bad in (None, 'absent'):
        with pytest.raises(console.CommandError):
            invoke(endpoint, args, [{'transactionId': transaction, 'account': bad}])
    with pytest.raises(console.CommandError, match='choose account'):
        invoke(endpoint, args, [{'transactionId': transaction, 'account': '旅行 🍏', 'accountId': account}])
    with pytest.raises(console.CommandError):
        invoke(endpoint, args, [{'transactionId': transaction, 'unknown': 1}])
    flags['preview_revision'] = True
    with pytest.raises(console.CommandError, match='during transaction preview'):
        invoke(endpoint, args, [{'transactionId': transaction, 'amount': 1}])
    flags['preview_revision'] = False
    args.dry_run, args.revision = True, 'old'
    with pytest.raises(console.CommandError, match='Preview revision is stale'):
        invoke(endpoint, args, [{'transactionId': transaction, 'amount': 1}])
    assert not flags['posts']


@pytest.mark.parametrize('fault', ['lost', 400, 409, 'different', 'foreign'])
def test_uncertain_or_refused_write_retains_exact_intent_without_retry(endpoint, fault):
    flags, _, _, _, transaction, _ = endpoint
    flags['fault'] = fault
    with pytest.raises(console.CommandError) as failure:
        invoke(endpoint, options(), [{'transactionId': transaction, 'amount': 1}])
    assert 'No automatic retry' in str(failure.value) and 'exact original intent' in str(failure.value)
    assert 'private connection information' not in str(failure.value)
    assert len(flags['posts']) == 1
    assert ('nothing new was saved' in str(failure.value)) == (fault in (400, 409))


@pytest.mark.parametrize('fault', ['count', 'ids', 'createdIds', 'none', 'missing'])
def test_replayed_receipts_are_verified(endpoint, fault):
    flags, _, _, _, transaction, _ = endpoint
    args = options()
    edits = [{'transactionId': transaction, 'note': 'replacement'}]
    saved = invoke(endpoint, args, edits)
    args.request_id, args.revision = saved['requestId'], saved['expectedRevision']
    flags['fault'] = fault
    with pytest.raises(console.CommandError, match='not confirmed'):
        invoke(endpoint, args, edits)
    assert len(flags['posts']) == 2


def test_console_edit_create_mixed_file_and_offline_help(endpoint, tmp_path, capsys, monkeypatch):
    flags, service, account, category, transaction, _ = endpoint
    def run(*args):
        assert console.main(['--home', str(tmp_path), 'Fictional', *args]) == 0
        return capsys.readouterr().out
    edited = json.loads(run('trnx', 'edit', transaction, '--merchant', 'Fictional corrected', '--memo', '', '--clear-category', '--agent', 'Codex'))
    assert edited['data']['changes'][0]['after']['categoryId'] is None
    assert edited['data']['changes'][0]['after']['note'] == ''
    assert json.loads(run('trnx', 'edit', transaction, '--note', 'Memo only'))['data']['changes'][0]['after']['note'] == 'Memo only'
    created = json.loads(run('trnx', 'create', '--account', account, '--date', '2026-11-03', '--amount', '-500', '--currency', 'USD', '--payee', 'Fictional café'))
    assert created['data']['changes'][0]['after']['source'] == 'manual'
    assert created['data']['changes'][0]['after']['reviewed'] is False
    assert 'new manual transaction' in run('trnx', 'create', '--account', '旅行 🍏', '--date', '2026-11-03', '--amount', '500', '--currency', 'USD', '--payee', 'Fictional café', '--category', category, '--dry-run', '--format', 'table')
    path = tmp_path / 'changes.json'
    path.write_text(json.dumps({'edits': [{'transactionId': transaction, 'merchant': '  Explicit fictional merchant  '}]}))
    assert json.loads(run('trnx', 'edit-many', '--file', str(path)))['operation'] == 'change_transactions'
    payload = {'edits': [{'transactionId': transaction, 'amount': 500}], 'creates': [{'accountId': account, 'date': '2026-11-02', 'merchant': 'Fictional split', 'amount': -10500, 'currency': 'USD'}]}
    path.write_text(json.dumps(payload))
    # Original now -10000; 500 + -10500 remains -10000.
    assert json.loads(run('trnx', 'edit-many', '--file', str(path), '--preserve-balances', '--dry-run'))['intent']['payload']['preserveBalances']
    monkeypatch.setattr(bulk_commands.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(json.dumps({'creates': payload['creates']}).encode())))
    assert json.loads(run('trnx', 'edit-many', '--file', '-', '--dry-run'))['data']['createdIds']
    for parts in ([], ['trnx'], ['trnx', 'edit'], ['trnx', 'create'], ['trnx', 'edit-many']):
        argv = [*parts, '--help'] if not parts else ['Fictional', *parts, '--help']
        with pytest.raises(SystemExit) as result:
            console.main(['--profile', '/does/not/exist', *argv])
        assert result.value.code == 0
        help_text = capsys.readouterr().out
        assert 'create' in help_text and 'edit' in help_text
    assert all(post['workspace'] == 'personal' for post in flags['posts'])


@pytest.mark.parametrize('value', [{}, {'creates': 1}, {'edits': [], 'preserveBalances': 1}, {'edits': [], 'creates': [], 'unexpected': 1}])
def test_mixed_file_shape_refusal(tmp_path, value):
    path = tmp_path / 'bad.json'
    path.write_text(json.dumps(value))
    with pytest.raises(console.CommandError, match='Cannot read bulk edits'):
        bulk_commands.read_edits(str(path), console.CommandError, complete=True)


def test_old_annotation_reader_rejects_new_shape(tmp_path):
    path = tmp_path / 'general.json'
    path.write_text('{"creates":[]}')
    with pytest.raises(console.CommandError, match='annotation reader'):
        bulk_commands.read_edits(str(path), console.CommandError)
