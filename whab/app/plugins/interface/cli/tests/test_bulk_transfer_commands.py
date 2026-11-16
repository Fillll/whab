"""Complete CLI intent, exact receipts and safe uncertain-response handling."""

import io
import json
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import bulk_transfers as bulk
from whab_plugin_cli import console
from whab_plugin_cli import transfer_commands as transfer


def arguments(**values):
    return SimpleNamespace(**{'target': 'Fictional 🍏', 'filter': 'match-transfers', 'reviewed': False,
        'revision': None, 'fingerprint': None, 'request_id': None, 'dry_run': False,
        'agent': None, 'batch': None, 'transaction_ids': ['out-1', 'in-1'],
        'since': None, 'until': None, 'offset': 0, 'limit': 100, **values})


@pytest.fixture
def endpoint(tmp_path):
    pairs = [{'outId': 'out-1', 'inId': 'in-1'}, {'outId': '出金', 'inId': '入金'}]
    path = tmp_path / 'pairs.json'
    path.write_text(json.dumps(pairs), encoding='utf-8')
    changes = []
    for pair in pairs:
        for name, other in [('outId', 'inId'), ('inId', 'outId')]:
            before = {'id': pair[name], 'merchant': '  Fictional  ', 'note': '[YNAB]  ', 'reviewed': False,
                      'accountId': 'account-' + name, 'date': '2026-11-01', 'amount': 100 if name == 'outId' else -100,
                      'currency': 'USD', 'pending': False, 'isTransfer': False, 'inferredTransfer': False,
                      'source': 'manual', 'categoryId': None, 'merchantLogo': None}
            changes.append({'id': pair[name], 'before': before, 'after': {**before, 'transferPeerId': pair[other], 'isTransfer': True}})
    state = {'calls': [], 'fault': None, 'data': {'action': 'match', 'fingerprint': 'f' * 64, 'pairCount': 2,
        'count': 4, 'changed': 4, 'reviewed': False, 'changes': changes}}
    def exchange(connection, method, url, **kwargs):
        body = kwargs['json']
        state['calls'].append(deepcopy(body))
        command = url.endswith('/commands')
        if command and state['fault'] == 'lost':
            raise httpx.ReadError('secret transport detail')
        if command and state['fault'] == 'refused':
            failure = console.CommandError('Pair invalid')
            failure.status_code = 409
            raise failure
        data = deepcopy(state['data'])
        if body['operation'] == 'transfer_audit':
            data = {'issues': [{'id': 'legacy-zero', 'code': 'zero', 'message': 'Zero hint', 'transaction': {'note': '[YNAB]'}, 'repairable': True}],
                    'total': 2, 'next': 1, 'zeroValuePolicy': 'Cannot establish directed legs'}
        if body['operation'] in {'preview_transfer_repair', 'repair_transfers'}:
            data.update(action='repair', count=2, changed=2, changes=deepcopy(state['data']['changes'][:2]))
            for row in data['changes']:
                row['after'].pop('transferPeerId', None)
                row['after']['isTransfer'] = False
        if (body.get('parameters') or body.get('payload')).get('reviewed'):
            data['reviewed'] = True
            for row in data['changes']:
                row['after']['reviewed'] = True
        if command and state['fault'] == 'changed':
            data['changes'][0]['after']['note'] = 'Unexpected'
        if command and state['fault'] == 'invalid':
            data = {'fingerprint': 'wrong'}
        if command and state['fault'] == 'missing':
            data.pop('changes')
        if command and type(state['fault']) is dict:
            if 'field' in state['fault']:
                data['changes'][0]['after'][state['fault']['field']] = state['fault']['value']
            else:
                data.update(state['fault'])
        return {'budgetId': 'fictional', 'revision': 'saved' if command else 'observed', 'data': data}
    return path, state, exchange


def run(endpoint, **values):
    path, _, exchange = endpoint
    return transfer.execute_transfer(None, 'fictional', '/budgets/fictional', arguments(file=str(path), **values), exchange, console.CommandError)


def test_complete_preview_and_atomic_optional_review_intent(endpoint):
    path, state, _ = endpoint
    preview = run(endpoint, dry_run=True, reviewed=True)
    assert preview['dryRun'] and len(state['calls']) == 1
    assert 'Read-only' in transfer.transfer_table(preview)
    assert all(row['after']['reviewed'] for row in preview['data']['changes'])
    saved = run(endpoint, reviewed=True, agent='Codex', batch='fictional', request_id='same-intent')
    assert saved['data'] == preview['data']
    assert saved['intent'] == {'operation': 'match_transfers', 'workspace': 'personal',
        'payload': {'pairs': json.loads(path.read_text()), 'reviewed': True, 'fingerprint': 'f' * 64},
        'expected_revision': 'observed', 'request_id': 'same-intent',
        'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional'}}
    table = transfer.transfer_table(saved)
    assert '2 pairs' in table and 'reviewed=true' in table and 'before:' in table and 'after:' in table
    assert '\\u51fa' in table and 'same-intent' in table
    assert len([body for body in state['calls'] if body['operation'] == 'match_transfers']) == 1


def test_explicit_mixed_zero_pairs_preserve_fields_and_exact_retry(endpoint):
    _, state, _ = endpoint
    for row in state['data']['changes'][2:]:
        row['before']['amount'] = row['after']['amount'] = 0
    preview = run(endpoint, dry_run=True, reviewed=True)
    intent = {'reviewed': True, 'revision': 'observed', 'fingerprint': 'f' * 64, 'request_id': 'zero-confirmed'}
    saved = run(endpoint, **intent)
    replay = run(endpoint, **intent)
    assert saved['data'] == replay['data'] == preview['data']
    assert state['calls'][1] == state['calls'][2]
    assert [row['after']['amount'] for row in saved['data']['changes']] == [100, -100, 0, 0]
    for row in saved['data']['changes']:
        for field in ('id', 'date', 'amount', 'accountId', 'merchant', 'note', 'categoryId'):
            assert row['before'][field] == row['after'][field]
        assert row['after']['reviewed'] and row['after']['transferPeerId']


def test_exact_recovery_skips_new_preview_and_preserves_original_intent(endpoint):
    _, state, _ = endpoint
    saved = run(endpoint, revision='observed', fingerprint='f' * 64, request_id='original')
    assert len(state['calls']) == 1 and state['calls'][0]['operation'] == 'match_transfers'
    assert saved['expectedRevision'] == 'observed' and saved['requestId'] == 'original'
    assert 'history' not in state['calls'][0]


@pytest.mark.parametrize('values', [{'revision': 'stale'}, {'fingerprint': 'old'}, {'revision': 'observed', 'fingerprint': 'old', 'dry_run': True}])
def test_changed_preview_refuses_before_submission(endpoint, values):
    with pytest.raises(console.CommandError, match='preview changed'):
        run(endpoint, **values)
    assert len(endpoint[1]['calls']) == 1


@pytest.mark.parametrize('fault', ['lost', 'refused', 'changed', 'invalid', 'missing'])
def test_uncertain_or_invalid_receipts_never_retry_and_keep_complete_intent(endpoint, fault):
    _, state, _ = endpoint
    state['fault'] = fault
    with pytest.raises(console.CommandError, match='COMPLETE original intent') as failure:
        run(endpoint, agent='Codex', batch='fictional')
    message = str(failure.value)
    assert 'secret transport detail' not in message
    assert ('refused; no new pairs' if fault == 'refused' else 'may have been saved') in message
    intent = state['calls'][-1]
    assert intent['request_id'] in message and intent['payload']['fingerprint'] in message
    assert 'out-1' in message and 'Codex' in message and 'fictional' in message
    assert len(state['calls']) == 2


@pytest.mark.parametrize('raw,match', [
    ('{}', 'array'), ('[]', 'array'), (json.dumps([{'outId': 'x', 'inId': 'y'}] * 101), 'array'),
    ('[null]', 'supply'), ('[{"outId":"x"}]', 'supply'),
    ('[{"outId":"x","outId":"z","inId":"y"}]', 'Duplicate'),
    ('[{"outId":"x","inId":"x"}]', 'disjoint'),
    ('[{"outId":"x","inId":"y"},{"outId":"y","inId":"z"}]', 'disjoint'),
    ('[{"outId":false,"inId":"y"}]', 'stable ID'),
    ('[{"outId":"","inId":"y"}]', 'stable ID'),
    (json.dumps([{'outId': 'x' * 201, 'inId': 'y'}]), 'stable ID'),
    ('[{"outId":"\\u0000","inId":"y"}]', 'stable ID'),
    ('[{"outId":"\\ud800","inId":"y"}]', 'surrogates'),
    ('not JSON', 'Cannot read'), (' ' * (2 * 1024 * 1024 + 1), 'exceeds'),
])
def test_bad_pair_files_fail_before_host_mutations(tmp_path, raw, match):
    path = tmp_path / 'bad.json'
    path.write_text(raw)
    with pytest.raises(console.CommandError, match=match):
        bulk.read_pairs(str(path), console.CommandError)


def test_file_and_stdin_reads_and_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(bulk.sys, 'stdin', SimpleNamespace(buffer=io.BytesIO(b'[{"outId":"out","inId":"in"}]')))
    assert bulk.read_pairs('-', console.CommandError) == [{'outId': 'out', 'inId': 'in'}]
    with pytest.raises(console.CommandError, match='Cannot read'):
        bulk.read_pairs(str(tmp_path / 'missing'), console.CommandError)
    path = tmp_path / 'invalid-utf8'
    path.write_bytes(b'\xff')
    with pytest.raises(console.CommandError, match='Cannot read'):
        bulk.read_pairs(str(path), console.CommandError)


def test_audit_and_explicit_repair_preview_saved_receipt(endpoint):
    _, state, _ = endpoint
    audited = run(endpoint, filter='transfer-audit', since='2026-01-01', until='2026-12-31', limit=1)
    assert audited['readOnly'] and audited['data']['next'] == 1
    assert state['calls'][-1]['parameters']['since'] == '2026-01-01'
    assert 'next offset: 1' in transfer.transfer_table(audited)
    preview = run(endpoint, filter='repair-transfers', dry_run=True)
    assert preview['dryRun'] and 'Read-only repair' in transfer.transfer_table(preview)
    saved = run(endpoint, filter='repair-transfers', agent='Codex', batch='cleanup')
    assert saved['data'] == preview['data']
    assert state['calls'][-1]['operation'] == 'repair_transfers'
    assert 'before:' in transfer.transfer_table(saved) and 'Request ID:' in transfer.transfer_table(saved)
    replay = run(endpoint, filter='repair-transfers', revision=saved['expectedRevision'], fingerprint=saved['fingerprint'], request_id=saved['requestId'])
    assert replay['data'] == saved['data']


@pytest.mark.parametrize('values', [{'revision': 'stale'}, {'fingerprint': 'old'}])
def test_stale_repair_preview_refuses_without_mutation(endpoint, values):
    with pytest.raises(console.CommandError, match='repair preview changed'):
        run(endpoint, filter='repair-transfers', **values)
    assert len(endpoint[1]['calls']) == 1


@pytest.mark.parametrize('fault', ['lost', 'refused', 'changed', 'invalid', 'missing'])
def test_repair_uncertainty_retains_intent_without_retry(endpoint, fault):
    endpoint[1]['fault'] = fault
    with pytest.raises(console.CommandError, match='COMPLETE original intent') as failure:
        run(endpoint, filter='repair-transfers')
    assert 'repair_transfers' in str(failure.value)
    assert len(endpoint[1]['calls']) == 2


@pytest.mark.parametrize('field,value', [('amount', 200), ('date', '2026-11-05'), ('accountId', 'other'),
    ('categoryId', 'other'), ('merchant', 'Fictional'), ('note', 'Changed'), ('isTransfer', False),
    ('reviewed', True), ('source', 'other'), ('unknownField', True)])
def test_forged_exact_bulk_retry_receipt_never_reports_verified(endpoint, field, value):
    endpoint[1]['fault'] = {'field': field, 'value': value}
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, revision='original', fingerprint='f' * 64, request_id='same-intent')
    assert len(endpoint[1]['calls']) == 1


@pytest.mark.parametrize('fields', [{'action': 'unmatch'}, {'pairCount': True}, {'count': '4'}, {'changed': 3}, {'reviewed': True}])
def test_forged_bulk_receipt_shape_rejected_on_exact_retry(endpoint, fields):
    endpoint[1]['fault'] = fields
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, revision='original', fingerprint='f' * 64, request_id='same-intent')


@pytest.mark.parametrize('field,value', [('amount', 200), ('note', 'Changed'), ('merchant', 'Fictional'),
    ('reviewed', True), ('transferPeerId', 'still-linked'), ('isTransfer', True), ('categoryId', 'new-category'),
    ('categoryAgent', 'Changed'), ('unknownField', True)])
def test_forged_repair_retry_receipt_never_reports_verified(endpoint, field, value):
    endpoint[1]['fault'] = {'field': field, 'value': value}
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, filter='repair-transfers', revision='original', fingerprint='f' * 64, request_id='same-intent')
    assert len(endpoint[1]['calls']) == 1


@pytest.mark.parametrize('fields', [{'action': 'match'}, {'count': '2'}, {'changed': -1}, {'changed': 3}])
def test_forged_repair_receipt_shape_rejected_on_exact_retry(endpoint, fields):
    endpoint[1]['fault'] = fields
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, filter='repair-transfers', revision='original', fingerprint='f' * 64, request_id='same-intent')


def test_repair_category_clear_metadata_and_zero_change_receipt(endpoint):
    for row in endpoint[1]['data']['changes']:
        row['before'].update(categoryId='orphan-transfer', categoryEdited=False, categoryAgent='Previous')
        row['after'].update(categoryId=None, categoryEdited=True)
    saved = run(endpoint, filter='repair-transfers')
    assert saved['data']['changes'][0]['after']['categoryEdited'] is True
    endpoint[1]['fault'] = {'changed': 0}
    # A no-op hint receipt remains a successful explicitly guarded intent.
    for row in endpoint[1]['data']['changes']:
        row['before'].update(categoryId=None, categoryEdited=True)
        row['before'].pop('categoryAgent', None)
    assert run(endpoint, filter='repair-transfers', revision='original', fingerprint='f' * 64)['data']['changed'] == 0


def test_repair_keeps_explicit_classification_without_effective_hint_override(endpoint):
    for row in endpoint[1]['data']['changes']:
        row['before'].update(categoryId='income-salary', categoryEdited=True, inferredTransfer=True)
        row['after'].update(categoryId='income-salary', categoryEdited=True, inferredTransfer=True)
    saved = run(endpoint, filter='repair-transfers')
    assert all(row['after']['categoryId'] == 'income-salary' and row['after']['inferredTransfer']
               and not row['after']['isTransfer'] for row in saved['data']['changes'])


@pytest.mark.parametrize('action', ['match-transfers', 'repair-transfers'])
@pytest.mark.parametrize('field', ['amount', 'categoryId', 'merchantLogo'])
def test_incomplete_both_sides_of_retry_receipt_are_refused(endpoint, action, field):
    for row in endpoint[1]['data']['changes']:
        row['before'].pop(field)
        row['after'].pop(field)
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, filter=action, revision='original', fingerprint='f' * 64, request_id='same-intent')


@pytest.mark.parametrize('parts', [[], ['trnx'], ['trnx', 'match-transfers'], ['trnx', 'transfer-audit'], ['trnx', 'repair-transfers']])
def test_offline_bulk_transfer_help_is_prominent_complete_and_copyable(parts, capsys):
    with pytest.raises(SystemExit) as exit:
        console.main(['--profile', '/missing', 'Fictional', *parts, '--help'])
    assert exit.value.code == 0
    text = capsys.readouterr().out
    if not parts or parts == ['trnx'] or parts[-1] == 'match-transfers':
        for expected in ('match-transfers', '--file -', 'outId', 'inId', '1–100', '--reviewed', '--fingerprint', 'ONE native atomic commit'):
            assert expected in text
        assert 'WHAB 2.82 with SQLite 1.30 or newer' in text
    if not parts or parts == ['trnx'] or parts[-1] != 'match-transfers':
        assert 'transfer-audit' in text and 'repair-transfers' in text and 'zero' in text
    assert 'offline' in text and 'uncertain' in text
