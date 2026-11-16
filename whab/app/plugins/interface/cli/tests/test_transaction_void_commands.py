"""Exact ordinary CLI void/unvoid intents, receipts, offline help and recovery."""

import json
from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import console
from whab_plugin_cli import transaction_voids as voids


def arguments(**values):
    return SimpleNamespace(**{'target': 'Fictional 🍏', 'filter': 'void', 'transaction_ids': ['tx-1', '旅行'],
        'revision': None, 'fingerprint': None, 'request_id': None, 'dry_run': False, 'agent': None, 'batch': None, **values})


@pytest.fixture
def endpoint():
    changes = []
    for identifier, source in [('tx-1', 'manual'), ('旅行', 'ynab')]:
        before = {'id': identifier, 'accountId': 'account-' + identifier, 'accountName': 'Fictional 🍏',
            'date': '2026-11-01', 'merchant': '  Fictional  ', 'merchantLogo': None, 'note': '[YNAB]\nExact  ',
            'amount': 1234, 'currency': 'USD', 'categoryId': 'expense', 'categoryAgent': 'Previous agent',
            'pending': False, 'reviewed': True, 'isTransfer': False, 'inferredTransfer': False,
            'categoryEdited': True, 'source': source, 'deleted': False}
        changes.append({'id': identifier, 'before': before, 'after': {**before, 'deleted': True, 'voided': True, 'reviewed': False}})
    data = {'action': 'void', 'count': 2, 'changed': 2, 'fingerprint': 'f' * 64, 'changes': changes,
        'balanceChanges': [{'accountId': 'account-tx-1', 'currency': 'USD', 'accountType': 'depository', 'before': 10000, 'after': 11234},
                           {'accountId': 'account-旅行', 'currency': 'USD', 'accountType': 'depository', 'before': 10000, 'after': 10000}]}
    state = {'data': data, 'calls': [], 'fault': None, 'preview_fault': None}
    def exchange(connection, method, path, **kwargs):
        if path.endswith('/budgets'):
            return {'budgets': [{'id': 'fictional', 'name': 'Fictional 🍏'}]}
        body = kwargs['json']
        state['calls'].append(deepcopy(body))
        command = path.endswith('/commands')
        if command and state['fault'] == 'lost':
            raise httpx.ReadError('private connection information')
        if command and state['fault'] in (400, 409):
            error = console.CommandError('Fictional refusal')
            error.status_code = state['fault']
            raise error
        result = deepcopy(state['data'])
        if 'unvoid' in body['operation']:
            result['action'] = 'unvoid'
            for row in result['changes']:
                row['before'] = {**row['after']}
                row['after']['voided'] = False
                row['after'].update(deleted=False, reviewed=False)
            for balance in result['balanceChanges']:
                balance['before'], balance['after'] = balance['after'], balance['before']
        fault = state['fault'] if command else state['preview_fault']
        if callable(fault):
            fault(result)
        return {'budgetId': 'foreign' if fault == 'foreign' else 'fictional',
                'revision': 'saved' if command else 'observed', 'data': result}
    return state, exchange


def run(endpoint, **values):
    return voids.execute_void(None, 'fictional', '/fictional', arguments(**values), endpoint[1], console.CommandError)


@pytest.mark.parametrize('action', ['void', 'unvoid'])
def test_preview_saved_exact_intent_and_full_table(endpoint, action):
    state, _ = endpoint
    preview = run(endpoint, filter=action, dry_run=True)
    assert len(state['calls']) == 1 and state['calls'][0]['operation'] == 'preview_' + action + '_transactions'
    assert preview['dryRun'] and 'Read-only' in voids.void_table(preview)
    saved = run(endpoint, filter=action, agent='Codex', batch='fictional', request_id='intent')
    assert saved['data'] == preview['data']
    assert saved['intent'] == {'operation': action + '_transactions', 'workspace': 'personal',
        'payload': {'transactionIds': ['tx-1', '旅行'], 'fingerprint': 'f' * 64},
        'expected_revision': 'observed', 'request_id': 'intent', 'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional'}}
    assert len(state['calls']) == 3
    table = voids.void_table(saved)
    assert all(text in table for text in ['before:', 'after:', 'balanceChanges', '11234', 'Request ID:', 'intent', '\\u65c5'])
    for row in saved['data']['changes']:
        assert not row['after']['reviewed']
        for field in ['amount', 'date', 'note', 'merchant', 'accountId', 'categoryAgent']:
            assert row['before'][field] == row['after'][field]


@pytest.mark.parametrize('action', ['void', 'unvoid'])
def test_exact_recovery_has_no_fresh_preview_and_preserves_metadata(endpoint, action):
    state, _ = endpoint
    saved = run(endpoint, filter=action, revision='original', fingerprint='f' * 64, request_id='original-id')
    assert len(state['calls']) == 1 and state['calls'][0]['operation'] == action + '_transactions'
    assert saved['expectedRevision'] == 'original' and saved['requestId'] == 'original-id'
    assert 'history' not in state['calls'][0]


@pytest.mark.parametrize('values', [{'revision': 'stale'}, {'fingerprint': 'old'},
    {'revision': 'stale', 'fingerprint': 'f' * 64, 'dry_run': True}])
def test_changed_preview_never_submits(endpoint, values):
    with pytest.raises(console.CommandError, match='preview changed'):
        run(endpoint, **values)
    assert len(endpoint[0]['calls']) == 1


@pytest.mark.parametrize('values,phrase', [([], '1–100'), (['x'] * 101, '1–100'), (['x', 'x'], 'duplicate'),
    ([''], 'nonempty'), (['x' * 201], '200'), (['\x00'], 'nonempty'), (['\ud800'], 'UTF-8'), ([None], 'nonempty')])
def test_bad_ids_refused_before_any_host_request(endpoint, values, phrase):
    with pytest.raises(console.CommandError, match=phrase):
        run(endpoint, transaction_ids=values)
    assert not endpoint[0]['calls']


@pytest.mark.parametrize('fault', ['lost', 400, 409, 'foreign'])
def test_uncertain_or_refused_write_never_retries(endpoint, fault):
    state, _ = endpoint
    state['fault'] = fault
    with pytest.raises(console.CommandError, match='COMPLETE original intent') as failure:
        run(endpoint, agent='Codex', batch='fictional')
    assert len(state['calls']) == 2
    message = str(failure.value)
    assert 'private connection information' not in message
    assert 'may have been saved' in message and 'nothing new was saved' not in message
    if fault in (400, 409):
        assert 'Server reported: Fictional refusal' in message
    assert state['calls'][-1]['request_id'] in message and 'Codex' in message and 'fictional' in message


@pytest.mark.parametrize('alter', [
    lambda d: d.update(action='other'), lambda d: d.update(count=True), lambda d: d.update(count=3),
    lambda d: d.update(changed=False), lambda d: d.update(changed=0), lambda d: d.update(fingerprint='wrong'),
    lambda d: d.update(changes=None), lambda d: d['changes'].reverse(), lambda d: d.update(balanceChanges=None),
    lambda d: d['changes'][0].update(before={'id': 'tx-1'}),
    lambda d: d['changes'][0].update(after={'id': 'tx-1'}),
    lambda d: d['changes'][0]['before'].update(id='wrong'), lambda d: d['changes'][0]['after'].update(id='wrong'),
    lambda d: d['changes'][0]['after'].update(reviewed=True), lambda d: d['changes'][0]['after'].update(deleted=False),
    lambda d: d['changes'][0]['before'].update(deleted=True), lambda d: d['changes'][0]['before'].update(voided=True),
    lambda d: d['changes'][0]['after'].pop('voided'),
    lambda d: d['changes'][0]['after'].update(amount=999), lambda d: d['changes'][0]['after'].update(merchant='trimmed'),
    lambda d: d['changes'][0]['after'].update(note='lost'), lambda d: d['changes'][0]['after'].update(providerDeleted=True),
    lambda d: d['balanceChanges'].pop(), lambda d: d['balanceChanges'].append(d['balanceChanges'][0]),
    lambda d: d['balanceChanges'][1].update(accountId='account-tx-1'),
    lambda d: d['balanceChanges'][0].update(accountId='foreign'), lambda d: d['balanceChanges'][0].update(currency='EUR'),
    lambda d: d['balanceChanges'][0].pop('accountType'), lambda d: d['balanceChanges'][0].update(accountType='unknown'),
    lambda d: d['balanceChanges'][0].update(after=8766),
    lambda d: d['balanceChanges'][0].update(before=True), lambda d: d['balanceChanges'][0].update(after=None),
    lambda d: d['balanceChanges'][1].update(after=10001), lambda d: d.pop('changes'),
])
def test_forged_recovery_receipts_are_uncertain_not_verified(endpoint, alter):
    endpoint[0]['fault'] = alter
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, revision='original', fingerprint='f' * 64, request_id='original-id')
    assert len(endpoint[0]['calls']) == 1


@pytest.mark.parametrize('alter', [lambda d: d['changes'][0]['before'].pop('voided'),
    lambda d: d['changes'][0]['before'].update(deleted=False),
    lambda d: d['changes'][0]['before'].update(providerDeleted=True),
    lambda d: d['changes'][0]['after'].update(voided=True)])
def test_unvoid_receipt_requires_prior_explicit_void_and_cleared_marker(endpoint, alter):
    endpoint[0]['fault'] = alter
    with pytest.raises(console.CommandError, match='may have been saved'):
        run(endpoint, filter='unvoid', revision='original', fingerprint='f' * 64)


def test_invalid_preview_refuses_and_complete_zero_receipt_is_valid(endpoint):
    state, _ = endpoint
    state['preview_fault'] = lambda d: d.update(count=0)
    with pytest.raises(console.CommandError, match='Invalid.*preview'):
        run(endpoint, dry_run=True)
    assert len(state['calls']) == 1
    state['preview_fault'] = None
    for row in state['data']['changes']:
        row['before']['amount'] = row['after']['amount'] = 0
    for row in state['data']['balanceChanges']:
        row['after'] = row['before']
    assert run(endpoint)['data']['count'] == 2


def test_same_account_currency_inconsistency_refused(endpoint):
    state, _ = endpoint
    row = state['data']['changes'][1]
    for side in ['before', 'after']:
        row[side].update(accountId='account-tx-1', currency='EUR')
    with pytest.raises(console.CommandError, match='Invalid.*preview'):
        run(endpoint)


@pytest.mark.parametrize('parts', [[], ['Fictional', '--help'], ['Fictional', 'trnx', '--help'],
    ['Fictional', 'trnx', 'void', '--help'], ['Fictional', 'trnx', 'unvoid', '--help']])
def test_offline_help_is_prominent_complete_without_profile(parts, capsys):
    with pytest.raises(SystemExit) as result:
        console.main(['--profile', '/missing-profile', *(parts or ['--help'])])
    assert result.value.code == 0
    text = capsys.readouterr().out
    assert all(phrase in text for phrase in ['trnx void', 'trnx unvoid', 'balanceChanges', '1–100', 'tombstones',
        '--dry-run', '--revision', '--fingerprint', '--request-id', 'provider', 'manual', 'both', 'uncertain', 'offline'])


@pytest.mark.parametrize('action,format', [('void', 'json'), ('unvoid', 'table')])
def test_real_console_argument_routing_and_global_options(endpoint, monkeypatch, capsys, action, format):
    monkeypatch.setattr(console, 'local_connection', lambda *a, **kw: nullcontext(None))
    monkeypatch.setattr(console, 'exchange', endpoint[1])
    assert console.main(['--home', '/fictional', 'Fictional 🍏', 'trnx', action, 'tx-1', '旅行',
                         '--environment', 'sandbox', '--format', format, '--agent', 'Codex', '--batch', 'run']) == 0
    output = capsys.readouterr().out
    assert json.loads(output)['data']['action'] == action if format == 'json' else 'Action: unvoid' in output


@pytest.mark.parametrize('action', ['void', 'unvoid'])
def test_unknown_imported_snapshots_preserved_and_liability_sign_verified(endpoint, action):
    state, _ = endpoint
    state['data']['balanceChanges'][1].update(before=None, after=None)
    state['data']['balanceChanges'][0].update(accountType='credit', after=8766)
    assert run(endpoint, filter=action)['data']['balanceChanges'][1]['after'] is None


@pytest.mark.parametrize('balance', [
    {'accountId': 'account-旅行', 'currency': 'USD', 'accountType': 'depository', 'after': None},
    {'accountId': 'account-旅行', 'currency': 'USD', 'accountType': 'depository', 'before': None},
    {'accountId': 'account-旅行', 'currency': 'USD', 'accountType': 'depository', 'before': None, 'after': 1},
])
def test_unknown_balance_receipt_cannot_invent_or_omit_snapshot(endpoint, balance):
    endpoint[0]['data']['balanceChanges'][1] = balance
    with pytest.raises(console.CommandError, match='Invalid.*preview'):
        run(endpoint)


def test_unknown_balance_cannot_claim_a_manual_adjustment(endpoint):
    endpoint[0]['data']['balanceChanges'][0].update(before=None, after=None)
    with pytest.raises(console.CommandError, match='Invalid.*preview'):
        run(endpoint)


@pytest.mark.parametrize('status', [400, 409])
def test_error_after_native_void_commit_never_claims_no_save(status):
    from whab_plugin_sqlite import Store

    from whab.plugins import LedgerService

    store = Store(':memory:')
    try:
        service = LedgerService(store, budget_id='fictional')
        account = service.add_account('personal', {'name': 'Fictional', 'type': 'depository', 'balance': 100000})
        identifier = service.add_transaction('personal', {'accountId': account, 'date': '2026-11-01',
            'merchant': 'Fictional', 'amount': 1234, 'categoryId': None, 'note': 'Preserve'})
        submitted = []
        def exchange(connection, method, path, **kwargs):
            body = kwargs['json']
            if path.endswith('/queries'):
                result = service.query(body['operation'], 'personal', parameters=body['parameters'])
                return {'budgetId': 'fictional', **result}
            submitted.append(deepcopy(body))
            service.command(body['operation'], 'personal', body['payload'], request_id=body['request_id'],
                            expected_revision=body['expected_revision'])
            # The native commit succeeded; a later transport/checkpoint error
            # has an HTTP status, but no public proof of an absent mutation.
            failure = console.CommandError(f'WHAB returned HTTP {status}: checkpoint result unknown')
            failure.status_code = status
            raise failure
        with pytest.raises(console.CommandError, match='may have been saved') as failure:
            voids.execute_void(None, 'fictional', '/fictional', arguments(transaction_ids=[identifier]), exchange, console.CommandError)
        assert len(submitted) == 1 and 'nothing new was saved' not in str(failure.value)
        assert 'checkpoint result unknown' in str(failure.value)
        assert submitted[0]['request_id'] in str(failure.value)
        saved = next(row for row in store.export_budget('personal')['transactions'] if row['id'] == identifier)
        assert saved['voided'] is True and saved['deleted'] is True
        intent = submitted[0]
        replay = service.command(intent['operation'], 'personal', intent['payload'], request_id=intent['request_id'],
                                 expected_revision=intent['expected_revision'])
        assert replay['data']['changes'][0]['after'] == {**saved, 'accountName': 'Fictional', 'currency': 'USD'}
    finally:
        store.close()
