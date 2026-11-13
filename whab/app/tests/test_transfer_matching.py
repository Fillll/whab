"""Fictional transfer proposals, explicit pair approval and native recovery."""

from copy import deepcopy

import pytest

from whab.domain.accounts import normalize_account
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state, portable_snapshot
from whab.domain.transfer_matching import candidates, plan, reconcile_matches, validate_matches
from whab.plugins import LedgerService, RequestConflict, RevisionConflict


def fictional_state():
    state = initial_state('personal', '2026-10')
    state['accounts'] = {identifier: normalize_account({
        'id': identifier, 'name': name, 'institution': '', 'type': 'depository',
        'balance': 100000, 'source': 'manual', 'currency': 'USD', 'stale': False,
        'balanceUnknown': False,
    }) for identifier, name in [('a', '🦊 Счёт A'), ('b', 'Fictional savings'), ('c', 'Fictional second savings')]}
    state['transactions'] = {identifier: {
        'id': identifier, 'accountId': account, 'date': day,
        'merchant': '  Fictional transfer 商店  ', 'merchantLogo': None, 'amount': amount,
        'categoryId': None, 'pending': False, 'reviewed': True, 'source': 'manual',
        'note': '[YNAB: uncleared]\nExact existing note  ', 'isTransfer': False,
        'inferredTransfer': False, 'categoryEdited': True, 'deleted': False,
        'categoryAgent': 'Previous agent',
    } for identifier, account, amount, day in [
        ('out', 'a', 12345, '2026-10-31'), ('in', 'b', -12345, '2026-11-02'),
    ]}
    return state


@pytest.fixture(params=['sqlite', 'json'])
def native_store(request, tmp_path):
    if request.param == 'sqlite':
        from whab_plugin_sqlite import Store
    else:
        from whab_plugin_json import Store
    path = tmp_path / ('fictional.sqlite' if request.param == 'sqlite' else 'fictional.json')
    store = Store(path, month='2026-11')
    store.restore_budget('personal', portable_snapshot(fictional_state()))
    yield store, path, Store
    store.close()


def test_proposals_are_read_only_show_alternatives_and_do_not_infer_from_merchant_aliases():
    state = fictional_state()
    state['transactions']['another'] = {**state['transactions']['in'], 'id': 'another', 'accountId': 'c', 'merchant': 'Unrelated refund'}
    before = deepcopy(state)
    result = candidates(state['accounts'], state['transactions'].values(), {'since': '2026-11-01', 'until': '2026-11-30', 'accountIds': ['b'], 'limit': 1})
    assert result['total'] == 1 and result['next'] is None
    result = candidates(state['accounts'], state['transactions'].values(), {'since': '2026-10-31', 'until': '2026-10-31', 'limit': 1})
    assert result['total'] == 2 and result['next'] == 1
    assert result['candidates'][0]['ambiguous'] is True
    assert result['requiresConfirmation'] and not result['reviewedAutomatically']
    assert result['candidates'][0]['transactions'][0]['accountName'] == '🦊 Счёт A'
    assert state == before
    second = candidates(state['accounts'], state['transactions'].values(), {'offset': 1, 'limit': 1})
    assert second['next'] is None
    assert result['candidates'][0]['transactionIds'] != second['candidates'][0]['transactionIds']


@pytest.mark.parametrize('change', [
    {'pending': True}, {'deleted': True}, {'amount': -12344}, {'amount': 0},
    {'accountId': 'a'}, {'categoryId': 'groceries'}, {'date': '2026-11-09'},
])
def test_ineligible_pairs_never_publish_partial_changes(change):
    state = fictional_state()
    state['transactions']['in'].update(change)
    before = deepcopy(state)
    with pytest.raises(StoreError):
        plan(state, ['out', 'in'], apply=True)
    assert state == before
    assert not candidates(state['accounts'], state['transactions'].values(), {})['candidates']


def test_pair_rules_scope_currency_archival_and_invalid_references():
    for update in [{'onBudget': False}, {'currency': 'EUR'}, {'closed': True}]:
        state = fictional_state()
        state['accounts']['b'].update(update)
        with pytest.raises(StoreError):
            plan(state, ['out', 'in'], apply=True)
        assert candidates(state['accounts'], state['transactions'].values(), {})['total'] == 0
    for identifiers in [['out'], ['out', 'out'], [True, 'in'], ['out', 'missing'], ['out', 'in', 'x']]:
        with pytest.raises(StoreError):
            plan(fictional_state(), identifiers, apply=True)


def test_exact_pair_preview_and_reconciliation_preserve_unrelated_fields():
    state = fictional_state()
    before = deepcopy(state)
    preview = plan(state, ['out', 'in'])
    assert state == before
    plan(state, ['out', 'in'], fingerprint=preview['fingerprint'], apply=True)
    validate_matches(state)
    for identifier, previous in before['transactions'].items():
        after = state['transactions'][identifier]
        assert after['transferPeerId'] == ('in' if identifier == 'out' else 'out')
        assert after['isTransfer'] and not after['reviewed']
        assert {k: v for k, v in after.items() if k not in {'transferPeerId', 'isTransfer', 'reviewed'}} == {
            k: v for k, v in previous.items() if k not in {'isTransfer', 'reviewed'}}
    with pytest.raises(StoreError, match='already matched'):
        plan(state, ['out', 'in'])
    invalid = deepcopy(state)
    invalid['transactions']['in']['amount'] = -12300
    with pytest.raises(StoreError, match='Invalid transfer pair'):
        validate_matches(invalid)
    reconcile_matches(invalid)
    assert all('transferPeerId' not in row and not row['reviewed'] for row in invalid['transactions'].values())
    assert all(not row['isTransfer'] for row in invalid['transactions'].values())
    with pytest.raises(StoreError, match='changed'):
        plan(before, ['out', 'in'], fingerprint='f' * 64, apply=True)


@pytest.mark.parametrize('parameters', [{'limit': 0}, {'offset': -1}, {'accountIds': ['foreign']}, {'currency': 'zzz'}, {'since': 'bad'}, {'since': '2026-12-01', 'until': '2026-01-01'}, {'extra': 1}])
def test_invalid_proposal_filters_reject(parameters):
    state = fictional_state()
    with pytest.raises((StoreError, ValueError)):
        candidates(state['accounts'], state['transactions'].values(), parameters)


def test_atomic_confirmation_receipt_restart_portable_restore_and_history(native_store, tmp_path):
    store, path, Store = native_store
    service = LedgerService(store, budget_id='fictional-budget')
    before = store.export_budget('personal')
    read = service.query('transfer_candidates', 'personal', parameters={})
    assert read['data']['total'] == 1 and store.export_budget('personal') == before
    preview = service.query('preview_transfer_match', 'personal', parameters={'transactionIds': ['out', 'in'], 'action': 'match'})
    payload = {'transactionIds': ['out', 'in'], 'action': 'match', 'fingerprint': preview['data']['fingerprint']}
    args = {'request_id': 'fictional-confirm', 'expected_revision': preview['revision'], 'history': {'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fixture-batch'}}
    generation = store.commit_evidence().generation
    result = service.command('match_transfer', 'personal', payload, **args)
    assert store.commit_evidence().generation == generation + 1
    saved = store.export_budget('personal')
    assert saved['accounts'] == before['accounts'] and saved['assignments'] == before['assignments']
    assert all(row.get('transferPeerId') and not row['reviewed'] for row in saved['transactions'])
    assert all(row['merchant'] == '  Fictional transfer 商店  ' and row['categoryAgent'] == 'Previous agent' for row in saved['transactions'])
    assert service.command('match_transfer', 'personal', payload, **args) == result
    assert store.commit_evidence().generation == generation + 1
    with pytest.raises(RequestConflict):
        service.command('match_transfer', 'personal', {**payload, 'action': 'unmatch'}, **args)
    with pytest.raises(RevisionConflict):
        service.command('match_transfer', 'personal', payload, request_id='new-intent', expected_revision=preview['revision'])
    files = store.export_budget_files()
    restored = Store(tmp_path / ('restored' + path.suffix), month='2026-11')
    try:
        restored.restore_budget_files('personal', files)
        assert restored.export_budget('personal')['transactions'] == saved['transactions']
    finally:
        restored.close()
    store.close()
    reopened = Store(path, month='2026-11')
    try:
        fresh = LedgerService(reopened, budget_id='fictional-budget')
        assert reopened.commit_evidence().generation == generation + 1
        assert fresh.query('transfer_candidates', 'personal', parameters={})['data']['total'] == 0
        entry = reopened.budget_history('personal')['entries'][0]
        assert entry['actor'] == 'Codex' and entry['batchId'] == 'fixture-batch' and entry['reversible']
        for action in ('undo', 'redo'):
            candidate = fresh.query('history_preview', 'personal', parameters={'action': action, 'target': entry['id']})
            fresh.command('history_apply', 'personal', {**{k: candidate['data'][k] for k in ('action', 'target', 'fingerprint')}}, request_id='fixture-' + action, expected_revision=candidate['revision'])
        preview = fresh.query('preview_transfer_match', 'personal', parameters={'transactionIds': ['out', 'in'], 'action': 'unmatch'})
        fresh.command('match_transfer', 'personal', {'transactionIds': ['out', 'in'], 'action': 'unmatch', 'fingerprint': preview['data']['fingerprint']}, request_id='fixture-unmatch', expected_revision=preview['revision'])
        assert all(not row.get('transferPeerId') and not row['reviewed'] for row in reopened.transactions('personal'))
    finally:
        reopened.close()


def test_matched_records_protect_category_financial_and_scope_edits(native_store):
    store, _, _ = native_store
    service = LedgerService(store)
    preview = service.query('preview_transfer_match', 'personal', parameters={'transactionIds': ['out', 'in'], 'action': 'match'})
    service.command('match_transfer', 'personal', {'transactionIds': ['out', 'in'], 'action': 'match', 'fingerprint': preview['data']['fingerprint']}, request_id='match', expected_revision=preview['revision'])
    saved = store.export_budget('personal')
    for callback in [lambda: store.edit_transaction('personal', 'out', category_id='groceries'),
                     lambda: store.edit_transaction_details('personal', 'out', {'amount': 5}),
                     lambda: store.update_account('personal', 'a', {'onBudget': False}),
                     lambda: store.classify_accounts('personal', [{'accountId': 'a', 'onBudget': False}])]:
        with pytest.raises(StoreError, match='Unmatch'):
            callback()
        assert store.export_budget('personal') == saved
    store.edit_transaction('personal', 'out', reviewed=True, note='New exact note')
    assert next(row for row in store.transactions('personal') if row['id'] == 'out')['transferPeerId'] == 'in'


def test_unsupported_backend_refuses_without_any_mutation():
    class ReadOnly:
        def __getattr__(self, name):
            raise AssertionError('A mutation or unrelated read was attempted: ' + name)
        supports_transfer_matching = None
    with pytest.raises(StoreError, match='Update the storage'):
        LedgerService(ReadOnly()).query('transfer_candidates', 'personal', parameters={})


def test_proposal_cap_is_explicit_and_bank_hint_never_proves_a_match():
    state = fictional_state()
    state['transactions']['out']['inferredTransfer'] = True
    state['transactions'].update({f'credit-{index}': {**state['transactions']['in'], 'id': f'credit-{index}'} for index in range(1000)})
    with pytest.raises(StoreError, match='1,000'):
        candidates(state['accounts'], state['transactions'].values(), {})
    result = candidates(state['accounts'], state['transactions'].values(), {'accountIds': ['c']})
    assert result['total'] == 0 and result['next'] is None


@pytest.fixture
def bank_store(native_store):
    _, path, Store = native_store
    store = Store(path.with_name('fictional-bank' + path.suffix), month='2026-11')
    yield store
    store.close()


@pytest.mark.parametrize('correction', ['unchanged', 'amount', 'pending', 'removed'])
def test_native_bank_correction_keeps_or_invalidates_both_reviewed_links(bank_store, correction):
    store = bank_store
    state = fictional_state()
    state['items']['fictional-bank'] = {'id': 'fictional-bank', 'token': 'fictional-encrypted', 'provider': 'plaid', 'institution': 'Fictional', 'cursor': None, 'lastSynced': None, 'status': 'connected', 'error': None}
    for row in state['accounts'].values():
        row.update(source='plaid', itemId='fictional-bank', institution='Fictional bank')
    for row in state['transactions'].values():
        row['source'] = 'plaid'
    store.save_item('fictional-bank', 'fictional-encrypted', 'Fictional')
    records = [{**{key: row[key] for key in ['id', 'accountId', 'date', 'merchant', 'amount', 'pending', 'source', 'inferredTransfer']}, 'suggestedCategoryId': None, 'pendingTransactionId': None} for row in state['transactions'].values()]
    store.apply_sync('fictional-bank', {'added': records, 'modified': [], 'removed': []}, 'fictional-start', accounts=list(state['accounts'].values()))
    for row in state['transactions'].values():
        store.edit_transaction('personal', row['id'], note=row['note'])
    preview = store.preview_transfer_match('personal', ['out', 'in'], 'match')
    store.apply_transfer_match('personal', ['out', 'in'], 'match', preview['fingerprint'])
    store.review_transactions('personal', ['out', 'in'], True)
    row = state['transactions']['out']
    imported = {key: row[key] for key in ['id', 'accountId', 'date', 'merchant', 'amount', 'pending', 'source', 'inferredTransfer']}
    imported.update(suggestedCategoryId=None, pendingTransactionId=None)
    if correction == 'amount':
        imported['amount'] += 1
    if correction == 'pending':
        imported['pending'] = True
    changes = {'added': [], 'modified': [] if correction == 'removed' else [imported], 'removed': ['out'] if correction == 'removed' else []}
    store.apply_sync('fictional-bank', changes, 'fictional-next', accounts=[])
    rows = {row['id']: row for row in store.export_budget()['transactions']}
    assert all(bool(row.get('transferPeerId')) == (correction == 'unchanged') for row in rows.values())
    assert all(row['reviewed'] == (correction == 'unchanged') for row in rows.values())
    assert rows['out']['note'] == state['transactions']['out']['note']
