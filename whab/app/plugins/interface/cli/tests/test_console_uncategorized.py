"""Uncategorized reads are independent of review and preserve the selected ledger."""

import json
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import httpx
import pytest
from whab_plugin_cli import console


def transaction(identifier, **changes):
    return {'id': identifier, 'accountId': 'checking', 'date': '2026-11-07',
            'merchant': 'Fictional shop', 'amount': 1250, 'currency': 'USD',
            'categoryId': None, 'reviewed': False, 'pending': False,
            'isTransfer': False, 'inferredTransfer': False, 'source': 'ynab', **changes}


@pytest.fixture
def uncategorized_transport(monkeypatch, tmp_path):
    rows = [transaction('a', reviewed=True, date='2026-10-01'),
            transaction('b', pending=True, source='plaid'),
            transaction('c', isTransfer=True, amount=-2000),
            transaction('d', inferredTransfer=True, pending=True, currency='EUR', amount=567),
            transaction('e', categoryId='food'), transaction('f', deleted=True),
            transaction('g', accountId='unavailable-account', currency='JPY', amount=12345),
            transaction('h', amount=0, accountId='tracking'),
            transaction('i', amount=-99)]
    calls = []
    state = {'rows': rows, 'account_budget': 'budget-a', 'account_revision': 'revision-one',
             'transaction_budget': 'budget-a', 'transaction_revision': 'revision-one'}

    def respond(request):
        calls.append(request)
        assert request.method == 'GET'
        if request.url.path.endswith('/budgets'):
            value = {'budgets': [{'id': 'budget-a', 'name': 'Fictional budget'},
                                 {'id': 'budget-b', 'name': 'Other budget'}]}
        elif request.url.path.endswith('/queries/transactions'):
            selected = state['rows'] if '/budget-a/' in request.url.path else []
            month = request.url.params.get('month')
            value = {'budgetId': state['transaction_budget'] if '/budget-a/' in request.url.path else 'budget-b',
                     'revision': state['transaction_revision'],
                     'data': [row for row in selected if not month or row['date'].startswith(month)]}
        else:
            assert request.url.path.endswith('/queries/accounts')
            value = {'budgetId': state['account_budget'] if '/budget-a/' in request.url.path else 'budget-b',
                     'revision': state['account_revision'],
                     'data': [{'id': 'checking', 'name': 'Fictional checking'},
                              {'id': 'tracking', 'name': 'Fictional tracking', 'onBudget': False}]}
        return httpx.Response(200, json=value)

    @contextmanager
    def connection(*args, **kwargs):
        with httpx.Client(base_url='https://localhost', transport=httpx.MockTransport(respond)) as client:
            yield client

    monkeypatch.setattr(console, 'local_connection', connection)
    monkeypatch.setattr(Path, 'home', lambda: tmp_path)
    monkeypatch.delenv('WHAB_HOME', raising=False)
    return calls, state


def read(capsys, *arguments):
    assert console.main(['Fictional budget', 'trnx', 'uncategorized', *arguments]) == 0
    return json.loads(capsys.readouterr().out)


def test_complete_history_counts_include_reviewed_transfers_pending_and_tracking(uncategorized_transport, capsys):
    calls, state = uncategorized_transport
    before = deepcopy(state['rows'])
    result = read(capsys)
    assert result['budgetId'] == 'budget-a' and result['revision'] == 'revision-one'
    assert result['total'] == result['returned'] == 7
    assert (result['pending'], result['transferFlagged'], result['postedNonTransfer']) == (2, 2, 4)
    assert [row['id'] for row in result['data']] == ['i', 'h', 'g', 'd', 'c', 'b', 'a']
    assert result['data'][-1]['reviewed'] is True
    assert result['data'][1]['accountName'] == 'Fictional tracking'
    assert result['data'][2]['accountName'] == 'unavailable-account'
    assert result['byCurrency'] == [{'currency': 'EUR', 'count': 1}, {'currency': 'JPY', 'count': 1}, {'currency': 'USD', 'count': 5}]
    assert result['bySource'] == [{'source': 'plaid', 'count': 1}, {'source': 'ynab', 'count': 6}]
    assert state['rows'] == before
    assert len(calls) == 3 and all(request.method == 'GET' for request in calls)
    assert dict(calls[1].url.params) == {'workspace': 'personal'}


def test_sample_retains_full_counts_and_uses_stable_date_id_order(uncategorized_transport, capsys):
    result = read(capsys, '--limit', '2')
    assert result['total'] == 7 and result['returned'] == 2 and result['pending'] == 2
    assert [row['id'] for row in result['data']] == ['i', 'h']


@pytest.mark.parametrize('flags,ids', [
    (['--posted'], ['i', 'h', 'g', 'c', 'a']),
    (['--exclude-transfers'], ['i', 'h', 'g', 'b', 'a']),
    (['--posted', '--exclude-transfers'], ['i', 'h', 'g', 'a']),
    (['--month', '2026-10'], ['a']),
])
def test_filters_apply_before_counts_and_limits(uncategorized_transport, capsys, flags, ids):
    result = read(capsys, *flags)
    assert [row['id'] for row in result['data']] == ids
    assert result['total'] == result['returned'] == len(ids)


def test_aliases_resolve_names_and_ids_without_cross_budget_results(uncategorized_transport, capsys):
    for target in ['fictional BUDGET', 'budget-a']:
        assert console.main([target, 'transactions', 'uncategorised', '--limit', '1']) == 0
        assert json.loads(capsys.readouterr().out)['total'] == 7
    assert console.main(['Other budget', 'trnx', 'uncategorized', '--format', 'table']) == 0
    output = capsys.readouterr().out
    assert '0 matched, 0 shown' in output and 'No matching transactions.' in output
    assert 'Fictional checking' not in output


@pytest.mark.parametrize('change', [
    {'account_budget': 'foreign'}, {'account_revision': 'different'},
    {'transaction_budget': 'foreign'}, {'transaction_revision': None},
])
def test_mismatched_budget_or_revision_fails_without_a_partial_report(uncategorized_transport, capsys, change):
    calls, state = uncategorized_transport
    state.update(change)
    assert console.main(['Fictional budget', 'trnx', 'uncategorized']) == 1
    output = capsys.readouterr()
    assert not output.out and 'error' in output.err
    assert all(request.method == 'GET' for request in calls)


@pytest.mark.parametrize('value', ['0', '-2', 'invalid'])
def test_invalid_sample_limit_fails_before_opening_a_connection(uncategorized_transport, capsys, value):
    with pytest.raises(SystemExit) as error:
        console.main(['Fictional budget', 'trnx', 'uncategorized', '--limit', value])
    assert error.value.code == 2 and not uncategorized_transport[0]
    capsys.readouterr()


def test_uncategorized_help_is_offline_and_describes_scope(uncategorized_transport, capsys):
    with pytest.raises(SystemExit) as error:
        console.main(['Fictional budget', 'trnx', 'uncategorized', '--help'])
    assert error.value.code == 0 and not uncategorized_transport[0]
    output = capsys.readouterr().out
    assert '--limit' in output and '--format' in output and 'all history' in output
    assert 'never categorizes, reviews or syncs' in output


def test_table_formats_currency_precision_and_escapes_controls(uncategorized_transport, capsys):
    _, state = uncategorized_transport
    state['rows'][0]['merchant'] = 'x' * 100
    state['rows'][1]['merchant'] = 'Fictional\x1b[31m\nshop'
    del state['rows'][-1]['source']
    assert console.main(['Fictional budget', 'trnx', 'uncategorized', '--format', 'table']) == 0
    output = capsys.readouterr().out
    assert '7 matched, 7 shown' in output
    assert 'Fictional checking' in output and 'Fictional tracking' in output
    assert '-$20.00' in output and '-$0.99' in output and '$0.00' in output
    assert 'EUR 5.67' in output and 'JPY 12,345' in output
    assert 'Pending' in output and 'Posted' in output and 'unknown' in output
    assert '\x1b' not in output and '\\u001b' in output and '\\u000a' in output and '…' in output
