"""Offline help, exact names, read-only proposals and guarded transfer confirmations."""

from copy import deepcopy
from types import SimpleNamespace

import httpx
import pytest
from whab_plugin_cli import console
from whab_plugin_cli import transfer_commands as transfer
from whab_plugin_sqlite import Store

from whab.plugins import LedgerService


@pytest.fixture
def endpoint():
    store = Store(':memory:', month='2026-11')
    service = LedgerService(store, budget_id='fictional')
    ids = []
    accounts = []
    for name, amount in [('旅行 🍏', 100), ('Сбережения', -100)]:
        account = service.add_account('personal', {'name': name, 'type': 'depository', 'balance': 10000})
        accounts.append(account)
        ids.append(service.add_transaction('personal', {'accountId': account, 'date': '2026-11-01', 'merchant': '  Fictional  ', 'amount': amount, 'categoryId': None, 'note': '[YNAB]  '}))
    state = {'fault': None, 'posts': []}
    def exchange(connection, method, path, **kwargs):
        if method == 'GET':
            result = service.query('accounts', 'personal')
        else:
            body = kwargs['json']
            if path.endswith('/queries'):
                result = service.query(body['operation'], 'personal', parameters=body['parameters'])
                if state['fault'] == 'revision':
                    result['revision'] = 'changed'
            else:
                state['posts'].append(deepcopy(body))
                if state['fault'] == 'lost':
                    raise httpx.ReadError('private detail')
                result = service.command(body['operation'], 'personal', body['payload'], request_id=body['request_id'], expected_revision=body['expected_revision'], history=body.get('history'))
        return {**result, 'budgetId': 'fictional'}
    yield service, ids, accounts, state, exchange
    store.close()


def args(**changes):
    return SimpleNamespace(**{'target': 'Fictional', 'filter': 'transfers', 'account': None, 'since': None, 'until': None, 'currency': None, 'offset': 0, 'limit': 100, 'revision': None, 'fingerprint': None, 'request_id': None, 'dry_run': False, 'agent': None, 'batch': None, **changes})


def run(endpoint, **changes):
    return transfer.execute_transfer(None, 'fictional', '/budgets/fictional', args(**changes), endpoint[-1], console.CommandError)


def test_proposals_names_tables_and_no_mutation(endpoint):
    service, ids, accounts, state, _ = endpoint
    revision = service.revision
    result = run(endpoint, account=['旅行 🍏', accounts[0]], since='2026-11-01', until='2026-11-01', currency='USD')
    assert result['data']['total'] == 1 and result['data']['next'] is None
    text = transfer.transfer_table(result)
    assert all(identifier in text for identifier in ids) and '旅行 🍏' in text
    assert service.revision == revision and not state['posts']
    state['fault'] = 'revision'
    with pytest.raises(console.CommandError, match='resolution'):
        run(endpoint, account=['旅行 🍏'])
    with pytest.raises(console.CommandError, match='Unknown or ambiguous'):
        run(endpoint, account=['Missing'])


def test_exact_preview_confirmation_replay_unmatch_attribution(endpoint):
    service, ids, _, state, _ = endpoint
    preview = run(endpoint, filter='match-transfer', transaction_ids=ids, dry_run=True)
    revision, fingerprint = preview['revision'], preview['data']['fingerprint']
    assert not state['posts'] and 'both need review' in transfer.transfer_table(preview)
    for changes in [{'revision': 'stale'}, {'fingerprint': 'x'}]:
        with pytest.raises(console.CommandError, match='preview changed'):
            run(endpoint, filter='match-transfer', transaction_ids=ids, **changes)
    saved = run(endpoint, filter='match-transfer', transaction_ids=ids, revision=revision, fingerprint=fingerprint, request_id='exact-original', agent='Codex', batch='fixture')
    assert saved['data'] == preview['data']
    assert state['posts'][-1]['history'] == {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fixture'}
    assert run(endpoint, filter='match-transfer', transaction_ids=ids, revision=revision, fingerprint=fingerprint, request_id='exact-original', agent='Codex', batch='fixture') == saved
    unmatched = run(endpoint, filter='unmatch-transfer', transaction_ids=ids)
    assert all('transferPeerId' not in row['after'] for row in unmatched['data']['changes'])
    assert 'none' in transfer.transfer_table(unmatched)


def test_uncertain_result_retains_exact_intent_without_retry(endpoint):
    _, ids, _, state, _ = endpoint
    state['fault'] = 'lost'
    with pytest.raises(console.CommandError, match='Retain original IDs/action') as error:
        run(endpoint, filter='match-transfer', transaction_ids=ids)
    assert len(state['posts']) == 1 and 'private detail' not in str(error.value)
    assert state['posts'][0]['request_id'] in str(error.value)


@pytest.mark.parametrize('parts', [[], ['trnx'], ['trnx', 'transfers'], ['trnx', 'match-transfer'], ['trnx', 'unmatch-transfer']])
def test_offline_help_lists_review_criteria_recovery_and_limit(parts, capsys):
    with pytest.raises(SystemExit) as exit:
        console.main(['--profile', '/missing', 'Fictional', *parts, '--help'])
    assert exit.value.code == 0
    text = capsys.readouterr().out
    assert 'Transfer proposals' in text and 'seven' in text and '1,000' in text
    assert 'reviewed=true' in text and 'uncertain' in text


def test_unfiltered_and_ambiguous_table_diagnostics(endpoint):
    result = run(endpoint)
    result['data']['candidates'][0]['ambiguous'] = True
    result['data']['next'] = 1
    assert 'Ambiguous alternatives' in transfer.transfer_table(result)
    assert 'next offset: 1' in transfer.transfer_table(result)
