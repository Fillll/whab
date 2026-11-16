"""Explicit exclusion preserves fictional money, source evidence and pair integrity."""

from copy import deepcopy

import pytest

from whab.domain import commands, history
from whab.domain.bank_sync import merge_sync
from whab.domain.budget import calculate_workspace
from whab.domain.categories import delete_category
from whab.domain.errors import StoreError
from whab.domain.imports import annotated_transaction, mark_provider_removed, sync_annotations
from whab.domain.ledger_state import initial_state, portable_snapshot, restore_snapshot
from whab.domain.money import MAX_CENTS
from whab.domain.portable import validate_portable_budget
from whab.domain.transaction_voids import plan, validate_void_ids
from whab.domain.transactions import transaction_rows
from whab.domain.transfer_matching import (
    archived_pair,
    audit,
    plan_many,
    reconcile_matches,
    valid_pair,
    validate_matches,
)


@pytest.fixture
def ledger():
    state = initial_state('personal', '2026-05')
    for identifier, kind in (('cash', 'depository'), ('card', 'credit'), ('other', 'depository')):
        commands.add_account(state, identifier, {'name': 'Fictional ' + identifier, 'type': kind, 'balance': 100000})
        state['accounts'][identifier]['balanceUnknown'] = False
    state['categories']['transfers'] = {'id': 'transfers', 'name': 'Transfers', 'group': 'Finance', 'kind': 'transfer', 'currency': 'USD', 'target': 0}
    state['categories']['opening'] = {'id': 'opening', 'name': 'Opening', 'group': 'Finance', 'kind': 'opening_balance', 'currency': 'USD', 'target': 0}
    for identifier, account, amount, source in (('imported🍏', 'cash', -12000, 'ynab'), ('manual', 'cash', 2100, 'manual'), ('debt', 'card', 3200, 'demo')):
        commands.add_transaction(state, identifier, {'accountId': account, 'date': '2026-05-02', 'merchant': 'Fictional café', 'amount': amount, 'categoryId': 'groceries'})
        state['transactions'][identifier].update(source=source, reviewed=True, merchant='  Fictional café 東京  ', note=' [YNAB: reconciled]\n  exact ', categoryAgent='Earlier agent')
    state['transactions']['imported🍏']['original'] = {'amount': -12500, 'merchant': ' Original provider merchant '}
    return state


def apply_plan(state, transaction_ids, **kwargs):
    kwargs.pop('apply', None)
    if 'fingerprint' not in kwargs:
        kwargs['fingerprint'] = plan(state, transaction_ids, **kwargs)['fingerprint']
    return plan(state, transaction_ids, apply=True, **kwargs)


def pair(state, amount=1000):
    for identifier, account, signed in (('out', 'cash', amount), ('in', 'other', -amount)):
        commands.add_transaction(state, identifier, {'accountId': account, 'date': '2026-05-03', 'merchant': 'Fictional transfer', 'amount': signed or 1, 'categoryId': None})
        state['transactions'][identifier].update(amount=signed, source='ynab')
    plan_many(state, [{'outId': 'out', 'inId': 'in'}], reviewed=True, apply=True)
    commands.edit_transaction(state, 'out', 'transfers', category_agent='Earlier agent')


def test_mixed_preview_apply_and_unvoid_preserve_exact_evidence_and_balance_policy(ledger):
    ledger['accounts']['cash'].update(stale=True, balanceUnknown=True, closed=True)
    before = deepcopy(ledger)
    ids = ['imported🍏', 'manual', 'debt']
    preview = plan(ledger, ids)
    assert ledger == before
    assert preview['count'] == preview['changed'] == 3
    assert preview['balanceChanges'] == [
        {'accountId': 'cash', 'currency': 'USD', 'accountType': 'depository', 'before': before['accounts']['cash']['balance'], 'after': before['accounts']['cash']['balance'] + 2100},
        {'accountId': 'card', 'currency': 'USD', 'accountType': 'credit', 'before': before['accounts']['card']['balance'], 'after': before['accounts']['card']['balance'] - 3200},
    ]
    assert apply_plan(ledger, ids, fingerprint=preview['fingerprint'], apply=True) == preview
    assert transaction_rows(ledger) == []
    for identifier in ids:
        assert ledger['transactions'][identifier] == {**before['transactions'][identifier], 'deleted': True, 'voided': True, 'reviewed': False}
    restored = apply_plan(ledger, ids, action='unvoid', apply=True)
    assert restored['action'] == 'unvoid'
    assert ledger['accounts'] == before['accounts']
    for identifier in ids:
        assert ledger['transactions'][identifier] == {**before['transactions'][identifier], 'reviewed': False, 'voided': False}
    assert ledger['categories'] == before['categories']


@pytest.mark.parametrize('amount', [0, 1234])
def test_both_pair_legs_archive_and_restore_without_reclassification_or_review(ledger, amount):
    pair(ledger, amount)
    ledger['accounts']['other'].update(closed=True, onBudget=False)
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='include every linked'):
        apply_plan(ledger, ['out'], apply=True)
    assert ledger == before
    apply_plan(ledger, ['in', 'out'], apply=True)
    row = ledger['transactions']['out']
    assert not valid_pair(row, ledger['transactions'], ledger['accounts'], ledger['categories'])
    assert archived_pair(row, ledger['transactions'], ledger['accounts'], ledger['categories'])
    validate_matches(ledger)
    assert not audit(ledger)['issues']
    reconcile_matches(ledger)
    assert row['transferPeerId'] == 'in' and row['categoryId'] == 'transfers'
    with pytest.raises(StoreError, match='include every linked'):
        apply_plan(ledger, ['in'], action='unvoid', apply=True)
    apply_plan(ledger, ['out', 'in'], action='unvoid', apply=True)
    assert valid_pair(ledger['transactions']['out'], ledger['transactions'], ledger['accounts'], ledger['categories'])
    assert ledger['accounts'] == before['accounts']
    assert all(ledger['transactions'][key] == {**before['transactions'][key], 'reviewed': False, 'voided': False} for key in ('in', 'out'))


@pytest.mark.parametrize('bad', [[], ['manual'] * 2, ['manual'] * 101, 'manual', [None], [''], ['x\x00'], ['\ud800'], ['x' * 201]])
def test_invalid_explicit_selection_rejected_without_mutation(ledger, bad):
    before = deepcopy(ledger)
    with pytest.raises(StoreError):
        apply_plan(ledger, bad, apply=True)
    assert ledger == before


def test_maximum_batch_is_one_detached_ordered_plan(ledger):
    template = ledger['transactions']['imported🍏']
    ids = [f'東京-{index}' for index in range(100)]
    for identifier in ids:
        ledger['transactions'][identifier] = {**deepcopy(template), 'id': identifier}
    validated = validate_void_ids(ids)
    assert validated == ids and validated is not ids
    result = apply_plan(ledger, ids, apply=True)
    assert result['count'] == 100 and [change['id'] for change in result['changes']] == ids
    assert result['balanceChanges'][0]['before'] == result['balanceChanges'][0]['after']


@pytest.mark.parametrize('corrupt', ['missing', 'deleted', 'provider', 'pair', 'incoming', 'category', 'account', 'overflow'])
def test_invalid_member_rejects_whole_mixed_batch(ledger, corrupt):
    ids = ['manual', 'imported🍏']
    row = ledger['transactions']['imported🍏']
    if corrupt == 'missing':
        ids.append('missing')
    elif corrupt == 'deleted':
        row['deleted'] = True
    elif corrupt == 'provider':
        row.update(providerDeleted=True)
    elif corrupt == 'pair':
        row['transferPeerId'] = 'manual'
        ledger['transactions']['manual']['transferPeerId'] = row['id']
    elif corrupt == 'incoming':
        ledger['transactions']['debt']['transferPeerId'] = row['id']
    elif corrupt == 'category':
        row['categoryId'] = 'transfers'
    elif corrupt == 'account':
        row['accountId'] = 'missing-account'
    else:
        ledger['accounts']['cash']['balance'] = MAX_CENTS
    before = deepcopy(ledger)
    with pytest.raises(StoreError):
        apply_plan(ledger, ids, apply=True)
    assert ledger == before


def test_stale_preview_same_action_and_provider_removal_are_actionable(ledger):
    preview = plan(ledger, ['manual'])
    ledger['transactions']['manual']['note'] += 'changed'
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='proposal changed'):
        apply_plan(ledger, ['manual'], fingerprint=preview['fingerprint'], apply=True)
    assert ledger == before
    with pytest.raises(StoreError, match='only an explicitly'):
        plan(ledger, ['manual'], action='unvoid')
    with pytest.raises(StoreError, match='Choose void'):
        plan(ledger, ['manual'], action='delete')
    apply_plan(ledger, ['imported🍏'], apply=True)
    with pytest.raises(StoreError, match='original request ID'):
        plan(ledger, ['imported🍏'])
    mark_provider_removed(ledger['transactions']['imported🍏'])
    with pytest.raises(StoreError, match='provider removed'):
        plan(ledger, ['imported🍏'], action='unvoid')


def test_manual_correction_reveals_deficit_without_reassigning_or_verifying(ledger):
    ledger['transactions'] = {'opening': {**ledger['transactions']['imported🍏'], 'id': 'opening', 'categoryId': 'opening', 'amount': -100000, 'source': 'manual'}}
    ledger['accounts']['cash']['balance'] = 100000
    ledger['accounts']['card']['onBudget'] = False
    ledger['accounts']['other']['onBudget'] = False
    ledger['budget'] = {'2026-05': {'groceries': 90000}}
    before = calculate_workspace(ledger, '2026-05')['summary']
    assert before['openingFunding'] == 100000
    assignments = deepcopy(ledger['budget'])
    balances = deepcopy(ledger['accounts'])
    apply_plan(ledger, ['opening'], apply=True)
    after = calculate_workspace(ledger, '2026-05')['summary']
    assert after['openingFunding'] == 0
    assert after['readyToAssign'] < 0 and after['fundingCapacity'] < 0
    assert ledger['budget'] == assignments
    assert ledger['accounts']['cash'] == {**balances['cash'], 'balance': 0}
    with pytest.raises(StoreError, match='available money'):
        commands.assign(ledger, 'groceries', '2026-05', 91000, as_of_month='2026-05')
    apply_plan(ledger, ['opening'], action='unvoid', apply=True)
    assert calculate_workspace(ledger, '2026-05')['summary'] == before


def test_guarded_history_and_portable_roundtrip_retain_exact_voided_pair(ledger):
    pair(ledger)
    before = deepcopy(ledger)
    apply_plan(ledger, ['out', 'in'], apply=True)
    history.record_states(before, ledger, {'operation': 'void_transactions', 'actor': 'Codex', 'actorKind': 'agent', 'batchId': 'fictional-void'})
    target = ledger['history']['entries'][-1]['id']
    saved = portable_snapshot(ledger)
    empty = initial_state('personal', '2026-05')
    restore_snapshot(empty, saved, mark_external_stale=False)
    assert empty['transactions'] == ledger['transactions']
    for action in ('undo', 'redo'):
        preview = history.public_preview(empty, action, target)
        old = deepcopy(empty)
        history.apply_request(empty, action, target, preview['fingerprint'])
        history.record_states(old, empty, {'operation': 'history', 'action': action, 'target': target})
        assert empty['transactions']['out'].get('voided', False) is (action == 'redo')
        assert empty['transactions']['out']['reviewed'] is (action == 'undo')
        validate_matches(empty)
    history.validate_history(empty['history'])
    assert empty['history']['entries'][0] == ledger['history']['entries'][0]


def test_history_refuses_partial_restoration_and_crossing_provider_removal(ledger):
    pair(ledger)
    before = deepcopy(ledger)
    apply_plan(ledger, ['out', 'in'], apply=True)
    history.record_states(before, ledger, {'operation': 'void_transactions'})
    malformed = portable_snapshot(ledger)
    row = next(row for row in malformed['transactions'] if row['id'] == 'out')
    row.pop('voided')
    row['deleted'] = False
    unchanged = deepcopy(ledger)
    with pytest.raises(StoreError, match='transfer pair'):
        history.apply_to_state(ledger, malformed)
    assert ledger == unchanged
    previous = deepcopy(ledger)
    mark_provider_removed(ledger['transactions']['out'])
    history.record_states(previous, ledger, {'operation': 'apply_sync'})
    with pytest.raises(StoreError, match='Later financial'):
        history.public_preview(ledger, 'undo', ledger['history']['entries'][0]['id'])


@pytest.mark.parametrize('change', [{'voided': 'false'}, {'providerDeleted': False}, {'voided': True}, {'providerDeleted': True}, {'voided': True, 'deleted': True, 'reviewed': True}])
def test_portable_rejects_inconsistent_tombstone_metadata(ledger, change):
    saved = portable_snapshot(ledger)
    saved['transactions'][0].update(change)
    with pytest.raises(StoreError):
        validate_portable_budget(saved)


def provider_record(**extra):
    return {'id': 'pending', 'accountId': 'linked', 'date': '2026-05-02', 'merchant': '  Fictional provider  ', 'amount': 1234, 'suggestedCategoryId': 'groceries', 'pending': True, 'source': 'plaid', 'inferredTransfer': False, 'pendingTransactionId': None, **extra}


def test_sync_freezes_tombstone_blocks_removed_restore_and_propagates_posted_lineage():
    state = initial_state('personal', '2026-05')
    state['items']['item'] = {'id': 'item', 'provider': 'plaid', 'token': 'fictional-ciphertext', 'cursor': None}
    account = {'id': 'linked', 'name': 'Fictional account', 'institution': 'Fictional bank', 'type': 'depository', 'balance': 100000, 'currency': 'USD', 'source': 'plaid', 'itemId': 'item'}

    def sync(*, added=(), modified=(), removed=()):
        return merge_sync(state, 'item', {'added': list(added), 'modified': list(modified), 'removed': list(removed)}, 'cursor', [account], synced_at='2026-05-03T00:00:00Z')

    sync(added=[provider_record()])
    commands.edit_transaction(state, 'pending', 'groceries', note=' [Imported evidence]\n  ', category_agent='Codex')
    state['receipts']['pending'] = b'fictional evidence'
    apply_plan(state, ['pending'], apply=True)
    original = deepcopy(state['transactions']['pending'])
    sync(modified=[provider_record(amount=9999, merchant='Changed provider')])
    assert state['transactions']['pending'] == original
    sync(removed=['pending'])
    assert state['transactions']['pending'] == {**original, 'providerDeleted': True}
    with pytest.raises(StoreError, match='provider removed'):
        plan(state, ['pending'], action='unvoid')
    sync(added=[provider_record()])
    assert state['transactions']['pending'] == original
    posted = provider_record(id='posted', pending=False, amount=1250, pendingTransactionId='pending')
    sync(added=[posted])
    replacement = state['transactions']['posted']
    assert replacement['voided'] and replacement['deleted'] and not replacement['reviewed']
    assert replacement['amount'] == 1250 and not replacement['pending']
    assert replacement['note'] == original['note'] and replacement['categoryAgent'] == 'Codex'
    assert state['transactions']['pending'] == {**original, 'providerDeleted': True}
    assert state['receipts'] == {'pending': b'fictional evidence', 'posted': b'fictional evidence'}
    assert state['accounts']['linked']['balance'] == account['balance']
    assert transaction_rows(state) == []
    apply_plan(state, ['posted'], action='unvoid', apply=True)
    assert [row['id'] for row in transaction_rows(state)] == ['posted']
    sync(modified=[posted])
    assert state['transactions']['posted']['voided'] is False
    assert not state['transactions']['posted']['deleted']
    sync(removed=['posted'])
    assert state['transactions']['posted']['deleted']
    assert state['transactions']['posted']['voided'] is False
    assert 'providerDeleted' not in state['transactions']['posted']
    validate_portable_budget(portable_snapshot(state))
    sync(added=[posted])
    assert [row['id'] for row in transaction_rows(state)] == ['posted']


def test_voided_pending_predecessor_cannot_be_bypassed_by_existing_posted_row():
    pending = annotated_transaction(provider_record(), {})
    pending.update(voided=True, deleted=True)
    existing = annotated_transaction(provider_record(id='posted', pending=False), {})
    annotations = sync_annotations(existing, pending)
    result = annotated_transaction(provider_record(id='posted', pending=False, amount=900), annotations)
    assert result == {**existing, 'voided': True, 'deleted': True, 'reviewed': False}
    assert sync_annotations(result, pending) is result
    assert sync_annotations(None, None) == {}


def test_unknown_balance_snapshot_is_preserved_and_manual_adjustment_refused(ledger):
    ledger['accounts']['cash'].update(balance=None, balanceUnknown=True, stale=True)
    result = apply_plan(ledger, ['imported🍏'], apply=True)
    assert result['balanceChanges'][0]['before'] is result['balanceChanges'][0]['after'] is None
    apply_plan(ledger, ['imported🍏'], action='unvoid', apply=True)
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='known numeric balance'):
        apply_plan(ledger, ['manual'], apply=True)
    assert ledger == before
    with pytest.raises(StoreError, match='Choose void'):
        plan(ledger, ['manual'], action=[])


@pytest.mark.parametrize('source', ['ynab', 'manual', 'demo'])
@pytest.mark.parametrize('kind', ['depository', 'credit', 'loan', 'other_asset'])
@pytest.mark.parametrize('amount', [-4321, 0, 4321])
def test_signed_manual_and_imported_balance_policy_for_every_amount_direction(ledger, source, kind, amount):
    account = ledger['accounts']['cash']
    account.update(type=kind, onBudget=kind in {'depository', 'credit'}, stale=True, balanceUnknown=True, closed=True)
    row = ledger['transactions']['imported🍏']
    row.update(source=source, amount=amount, pending=amount == 0)
    before = deepcopy(ledger)
    result = apply_plan(ledger, [row['id']], apply=True)
    effect = amount if kind in {'credit', 'loan'} else -amount
    expected = account['balance'] if source == 'ynab' else before['accounts']['cash']['balance'] - effect
    assert result['balanceChanges'][0]['after'] == expected
    assert ledger['accounts']['cash'] == {**before['accounts']['cash'], 'balance': expected}
    apply_plan(ledger, [row['id']], action='unvoid', apply=True)
    assert ledger['accounts'] == before['accounts']
    assert ledger['transactions'][row['id']]['amount'] == amount


def test_voided_category_references_and_source_identity_cannot_be_deleted_or_reimported(ledger):
    from whab.domain.ledger_state import require_pristine
    apply_plan(ledger, ['imported🍏', 'manual', 'debt'], apply=True)
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='deleted transactions reference'):
        delete_category(ledger, 'groceries')
    with pytest.raises(StoreError, match='empty personal workspace'):
        require_pristine(ledger)
    assert ledger == before


def test_void_unvoid_undo_redo_preserve_manual_account_and_exact_audit(ledger):
    original = deepcopy(ledger)
    for operation, action in (('void_transactions', 'void'), ('unvoid_transactions', 'unvoid')):
        previous = deepcopy(ledger)
        apply_plan(ledger, ['manual', 'debt'], action=action, apply=True)
        history.record_states(previous, ledger, {'operation': operation, 'actor': 'Codex', 'actorKind': 'agent'})
    target = ledger['history']['entries'][-1]['id']
    saved = deepcopy(ledger)
    for action in ('undo', 'redo'):
        preview = history.public_preview(ledger, action, target)
        previous = deepcopy(ledger)
        history.apply_request(ledger, action, target, preview['fingerprint'])
        history.record_states(previous, ledger, {'operation': 'history', 'action': action, 'target': target})
        assert ledger['transactions']['manual']['voided'] is (action == 'undo')
        assert not ledger['transactions']['manual']['reviewed']
    assert ledger['accounts'] == original['accounts']
    assert ledger['transactions'] == saved['transactions']
    history.validate_history(ledger['history'])


def test_voided_transfer_funding_and_activity_excluded_then_restored(ledger):
    pair(ledger)
    ledger['accounts']['other']['onBudget'] = False
    before = calculate_workspace(ledger, '2026-05')['summary']
    apply_plan(ledger, ['out', 'in', 'manual'], apply=True)
    excluded = calculate_workspace(ledger, '2026-05')['summary']
    assert excluded['transferFunding'] == 0 and before['transferFunding'] == -1000
    assert excluded['spent'] == before['spent'] - 2100
    apply_plan(ledger, ['out', 'in', 'manual'], action='unvoid', apply=True)
    assert calculate_workspace(ledger, '2026-05')['summary'] == before


def test_unvoid_unknown_credit_does_not_create_spendable_funding(ledger):
    ledger['accounts']['card']['balanceUnknown'] = True
    pair(ledger)
    apply_plan(ledger, ['out', 'in'], apply=True)
    apply_plan(ledger, ['out', 'in'], action='unvoid', apply=True)
    assert calculate_workspace(ledger, '2026-05')['summary']['fundingCapacity'] <= 0
    with pytest.raises(StoreError, match='unknown credit'):
        commands.assign(ledger, 'rent', '2026-05', 100, as_of_month='2026-05')

@pytest.mark.parametrize('fingerprint', [None, '', 'bad', 123, 'G' * 64, []])
def test_mutation_requires_exact_preview_fingerprint(ledger, fingerprint):
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='exact 64-character fingerprint'):
        plan(ledger, ['manual'], fingerprint=fingerprint, apply=True)
    assert ledger == before
