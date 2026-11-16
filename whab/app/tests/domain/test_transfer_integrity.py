"""Fictional strict pairs, atomic plans, legacy repair and funding boundaries."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace, ensure_assignment_allowed
from whab.domain.categories import classify_category
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state
from whab.domain.review import review_status
from whab.domain.transfer_matching import (
    audit,
    candidates,
    plan,
    plan_many,
    repair_plan,
    valid_pair,
    validate_pairs,
    validate_repair_ids,
)


def state_for_pairs(count=1):
    state = initial_state('personal', '2026-10')
    state.update(accounts={}, categories={}, transactions={}, budget={})
    for identifier, on_budget in [('cash', True), ('other', True)]:
        state['accounts'][identifier] = {'id': identifier, 'name': 'Счёт 🦊 ' + identifier,
            'institution': '', 'type': 'depository', 'subtype': 'checking', 'onBudget': on_budget,
            'currency': 'USD', 'balance': 100000, 'source': 'manual', 'stale': False,
            'balanceUnknown': False, 'closed': False}
    state['categories']['transfer'] = {'id': 'transfer', 'name': 'Transfers', 'group': 'Finance', 'target': 0, 'kind': 'transfer'}
    state['categories']['expense'] = {'id': 'expense', 'name': 'Food', 'group': 'Everyday', 'target': 0}
    for index in range(count):
        for identifier, account, amount in [('out', 'cash', 10000 + index), ('in', 'other', -10000 - index)]:
            key = f'{identifier}-{index}'
            state['transactions'][key] = {'id': key, 'accountId': account, 'date': '2026-10-15',
                'merchant': '  Fictional exact 商店  ', 'amount': amount, 'categoryId': None,
                'pending': False, 'reviewed': False, 'source': 'ynab', 'note': 'Original\n[YNAB: reconciled] ',
                'isTransfer': True, 'inferredTransfer': True, 'categoryEdited': False, 'deleted': False}
    return state


def pairs(count):
    return [{'outId': f'out-{index}', 'inId': f'in-{index}'} for index in range(count)]


@pytest.mark.parametrize('count', [1, 10, 50, 100])
def test_complete_explicit_batches_are_previewed_then_applied_once(count):
    state = state_for_pairs(count)
    before = deepcopy(state)
    preview = plan_many(state, pairs(count), reviewed=True)
    assert state == before
    assert preview['pairCount'] == count and preview['count'] == count * 2
    saved = plan_many(state, pairs(count), reviewed=True, fingerprint=preview['fingerprint'], apply=True)
    assert saved == preview
    assert state['accounts'] == before['accounts']
    for identifier, row in state['transactions'].items():
        assert valid_pair(row, state['transactions'], state['accounts'], state['categories'])
        assert row['reviewed']
        assert {key: value for key, value in row.items() if key not in {'transferPeerId', 'reviewed'}} == {key: value for key, value in before['transactions'][identifier].items() if key != 'reviewed'} | {'isTransfer': True}
        assert review_status(row, state['categories'], transactions=state['transactions'], accounts=state['accounts'])['eligible']


@pytest.mark.parametrize('mutate', [
    lambda state: state['transactions'].pop('in-1'),
    lambda state: state['transactions']['in-1'].update(amount=-1),
    lambda state: state['transactions']['in-1'].update(pending=True),
    lambda state: state['transactions']['in-1'].update(deleted=True),
    lambda state: state['transactions']['in-1'].update(categoryId='expense'),
    lambda state: state['transactions']['in-1'].update(date='2026-10-23'),
    lambda state: state['transactions']['in-1'].update(amount=0),
])
def test_any_invalid_late_pair_rejects_whole_batch(mutate):
    state = state_for_pairs(2)
    mutate(state)
    before = deepcopy(state)
    with pytest.raises(StoreError):
        plan_many(state, pairs(2), apply=True)
    assert state == before


def test_shared_counterpart_and_incoming_reference_reject_without_partial_linking():
    state = state_for_pairs(2)
    with pytest.raises(StoreError, match='duplicate'):
        plan_many(state, [pairs(2)[0], {'outId': 'out-1', 'inId': 'in-0'}], apply=True)
    state['transactions']['out-1']['transferPeerId'] = 'in-0'
    before = deepcopy(state)
    with pytest.raises(StoreError, match='already references'):
        plan_many(state, pairs(1), apply=True)
    assert state == before


def test_exact_fingerprint_covers_notes_account_scope_and_review_intent():
    state = state_for_pairs()
    preview = plan_many(state, pairs(1))
    for change in ('note', 'scope', 'review'):
        altered = deepcopy(state)
        if change == 'note':
            altered['transactions']['in-0']['note'] += ' changed'
        if change == 'scope':
            altered['accounts']['other']['onBudget'] = False
        before = deepcopy(altered)
        with pytest.raises(StoreError, match='changed'):
            plan_many(altered, pairs(1), reviewed=change == 'review', fingerprint=preview['fingerprint'], apply=True)
        assert altered == before


def test_new_orphan_transfer_category_and_review_rejected_but_legacy_auditable():
    state = state_for_pairs()
    before = deepcopy(state)
    with pytest.raises(StoreError, match='exactly two'):
        commands.edit_transaction(state, 'out-0', category_id='transfer')
    with pytest.raises(StoreError):
        commands.edit_transaction(state, 'out-0', reviewed=True)
    with pytest.raises(StoreError, match='exactly two'):
        commands.add_transaction(state, 'new', {'accountId': 'cash', 'date': '2026-10-15', 'merchant': 'Fictional', 'amount': 100, 'categoryId': 'transfer'})
    state['transactions']['out-0']['categoryId'] = 'expense'
    with pytest.raises(StoreError, match='exactly two'):
        classify_category(state, 'expense', 'transfer')
    state = before
    state['transactions']['out-0'].update(categoryId='transfer', reviewed=True)
    assert not review_status(state['transactions']['out-0'], state['categories'])['eligible']
    result = audit(state, {'since': '2026-10-15', 'until': '2026-10-15', 'limit': 1})
    assert result['total'] == 2 and result['next'] == 1
    assert result['issues'][0]['code'] == 'missing_pair'


def test_unpaired_zero_value_hint_is_unreviewed_and_repair_preserves_evidence():
    state = state_for_pairs()
    row = state['transactions']['out-0']
    row.update(amount=0, categoryId='transfer', reviewed=True)
    before = deepcopy(state)
    issues = audit(state)['issues']
    assert next(item for item in issues if item['id'] == row['id'])['code'] == 'zero_amount'
    preview = repair_plan(state, [row['id']])
    assert state == before
    result = repair_plan(state, [row['id']], fingerprint=preview['fingerprint'], apply=True)
    assert result == preview
    after = state['transactions'][row['id']]
    assert after['amount'] == 0 and after['note'] == row['note'] and after['merchant'] == row['merchant']
    assert after['inferredTransfer'] and after['isTransfer'] and not after['reviewed'] and after['categoryId'] is None
    with pytest.raises(StoreError, match='both IDs must identify existing posted zero-value'):
        plan_many(state, pairs(1))


def test_repair_requires_complete_broken_graph_and_stale_fingerprint_rejects():
    state = state_for_pairs()
    plan(state, ['out-0', 'in-0'], apply=True)
    state['transactions']['in-0']['amount'] = -500
    before = deepcopy(state)
    with pytest.raises(StoreError, match='every linked counterpart'):
        repair_plan(state, ['out-0'], apply=True)
    assert state == before
    preview = repair_plan(state, ['out-0', 'in-0'])
    with pytest.raises(StoreError, match='changed'):
        repair_plan(state, ['out-0', 'in-0'], fingerprint='0' * 64, apply=True)
    assert state == before
    repair_plan(state, ['out-0', 'in-0'], fingerprint=preview['fingerprint'], apply=True)
    assert all(not row.get('transferPeerId') and not row['reviewed'] for row in state['transactions'].values())


def test_projection_proof_is_read_only_and_never_authorizes_a_mutation():
    state = state_for_pairs()
    row = state['transactions']['out-0']
    row.update(categoryId='transfer', transferPairValid=True, transferPeerId='in-0')
    assert review_status(row, state['categories'])['eligible']
    with pytest.raises(StoreError):
        commands.edit_transaction(state, row['id'], reviewed=True)


@pytest.mark.parametrize('on_budget_out', [True, False])
@pytest.mark.parametrize('closed', [True, False])
def test_closed_cross_scope_cash_pair_has_signed_funding_not_income_spending(on_budget_out, closed):
    state = state_for_pairs()
    state['accounts']['cash'].update(onBudget=on_budget_out, closed=closed)
    state['accounts']['other']['onBudget'] = not on_budget_out
    before_accounts = deepcopy(state['accounts'])
    receipt = plan_many(state, pairs(1), reviewed=True, apply=True)
    assert receipt['pairs'][0]['crossesBudgetScope']
    assert state['accounts'] == before_accounts
    projected = calculate_workspace(state, '2026-10')
    summary = projected['summary']
    assert summary['income'] == summary['spent'] == 0
    assert summary['transferFunding'] == (-10000 if on_budget_out else 10000)
    assert summary['readyToAssign'] == 100000
    following = calculate_workspace(state, '2026-11')['summary']
    assert following['transferFunding'] == 0 and following['readyToAssign'] == 100000


@pytest.mark.parametrize('credit_balance,expected', [(5000, 10000), (-5000, 5000), (-15000, 0)])
def test_cross_scope_credit_paydown_never_creates_cash_from_overpayment(credit_balance, expected):
    state = state_for_pairs()
    state['accounts']['cash']['onBudget'] = False
    state['accounts']['other'].update(type='credit', subtype='credit_card', balance=credit_balance)
    plan_many(state, pairs(1), apply=True)
    summary = calculate_workspace(state, '2026-10')['summary']
    assert summary['transferFunding'] == expected
    assert summary['income'] == summary['spent'] == 0
    assert summary['readyToAssign'] == -max(0, credit_balance)


@pytest.mark.parametrize('credit_balance', [5000, -5000])
def test_unknown_credit_pair_cannot_unlock_spendable_funding_or_assignments(credit_balance):
    state = state_for_pairs()
    state['accounts']['cash']['onBudget'] = False
    state['accounts']['other'].update(type='credit', subtype='credit_card', balance=credit_balance,
                                      balanceUnknown=True, stale=True)
    original_accounts = deepcopy(state['accounts'])
    preview = plan_many(state, pairs(1))
    plan_many(state, pairs(1), fingerprint=preview['fingerprint'], apply=True)
    assert state['accounts'] == original_accounts
    for month in ['2026-10', '2026-11']:
        summary = calculate_workspace(state, month)['summary']
        assert summary['readyToAssign'] <= 0
        assert summary['fundingCapacity'] <= 0
        with pytest.raises(StoreError, match='unknown credit account balances'):
            ensure_assignment_allowed(state, 'expense', month, 1, month)
    assert calculate_workspace(state, '2026-10')['summary']['transferFunding'] > 0
    assert state['budget'] == {}


def test_same_scope_and_unverified_cash_do_not_create_transfer_funding():
    state = state_for_pairs()
    plan_many(state, pairs(1), apply=True)
    assert calculate_workspace(state, '2026-10')['summary']['transferFunding'] == 0
    state['accounts']['cash']['onBudget'] = False
    state['accounts']['other']['stale'] = True
    assert calculate_workspace(state, '2026-10')['summary']['transferFunding'] == 0


@pytest.mark.parametrize('value', [[], [{}], [{'outId': True, 'inId': 'i'}], [{'outId': '\ud800', 'inId': 'i'}], pairs(101)])
def test_invalid_batch_shapes(value):
    with pytest.raises(StoreError):
        validate_pairs(value)


def test_repair_limits_and_invalid_audit_filters():
    for ids in ([], ['same', 'same'], ['x'] * 201):
        with pytest.raises(StoreError):
            validate_repair_ids(ids)
    for parameters in ({'extra': True}, {'offset': -1}, {'limit': 101}, {'since': 'bad'}, {'since': '2026-11-01', 'until': '2026-10-01'}):
        with pytest.raises((StoreError, ValueError)):
            audit(state_for_pairs(), parameters)


@pytest.mark.parametrize('mode', ['category', 'balance'])
def test_same_scope_month_boundary_pair_tracks_dated_funding_and_rollover(mode):
    state = state_for_pairs()
    state['transactions']['out-0']['date'] = '2026-10-31'
    state['transactions']['in-0']['date'] = '2026-11-02'
    state['budget'] = {'2026-10': {'expense': 5000}, '2026-11': {'expense': 5000}}
    state['rolloverModes'] = {'2026-10': {'USD': mode}}
    plan_many(state, pairs(1), apply=True)
    october = calculate_workspace(state, '2026-10')
    november = calculate_workspace(state, '2026-11')
    assert october['summary']['transferFunding'] == -10000
    assert november['summary']['transferFunding'] == 10000
    assert october['summary']['income'] == november['summary']['income'] == 0
    assert october['summary']['spent'] == november['summary']['spent'] == 0
    assert october['summary']['readyToAssign'] == 185000
    assert november['summary']['readyToAssign'] == (190000 if mode == 'category' else 195000)
    assert november['transactions'][0]['transferPairValid'] is True
    for projection in (october, november):
        s = projection['summary']
        assert s['readyToAssign'] == s['income'] + s['openingFunding'] + s['transferFunding'] + s['carriedIn'] - s['assigned'] - s['assignedNextMonth']


def test_same_scope_credit_overpayment_is_reserved_outside_spendable_cash():
    state = state_for_pairs()
    state['accounts']['other'].update(type='credit', subtype='credit_card', balance=-5000)
    plan_many(state, pairs(1), apply=True)
    result = calculate_workspace(state, '2026-10')['summary']
    assert result['transferFunding'] == -5000
    assert result['readyToAssign'] == state['accounts']['cash']['balance']


def test_provider_transfer_suggestion_is_hint_while_saved_legacy_annotation_stays_readable():
    from whab.domain.imports import annotated_transaction

    state = state_for_pairs()
    record = {'id': 'new', 'accountId': 'cash', 'date': '2026-10-15',
              'merchant': '  Provider merchant  ', 'amount': 10000, 'source': 'plaid',
              'pending': False, 'inferredTransfer': False, 'suggestedCategoryId': 'transfer', 'pendingTransactionId': None}
    new = annotated_transaction(record, {}, categories=state['categories'])
    assert new['categoryId'] is None and new['isTransfer'] and new['inferredTransfer']
    assert not new['reviewed'] and new['merchant'] == record['merchant']
    legacy = {**state['transactions']['out-0'], 'categoryId': 'transfer', 'categoryEdited': True, 'reviewed': True}
    retained = annotated_transaction({**record, 'id': legacy['id']}, legacy, categories=state['categories'])
    assert retained['categoryId'] == legacy['categoryId'] and retained['reviewed']
    assert retained['note'] == legacy['note']
    plan(state, ['out-0', 'in-0'], apply=True)
    paired = {**state['transactions']['out-0'], 'categoryId': 'transfer', 'categoryEdited': True, 'reviewed': True}
    retained = annotated_transaction({**record, 'id': paired['id']}, paired, categories=state['categories'])
    assert retained['categoryId'] == 'transfer' and retained['reviewed'] and retained['transferPeerId'] == 'in-0'


def test_legacy_effective_transfer_flag_with_expense_cannot_bypass_two_leg_review():
    state = state_for_pairs()
    row = state['transactions']['out-0']
    row.update(categoryId='expense', isTransfer=True, inferredTransfer=True)
    with pytest.raises(StoreError, match='exactly two'):
        commands.edit_transaction(state, row['id'], reviewed=True)
    assert any(issue['id'] == row['id'] for issue in audit(state)['issues'])
    commands.edit_transaction(state, row['id'], category_id='expense', reviewed=True)
    assert row['isTransfer'] is False and row['inferredTransfer'] is True
    assert row['reviewed'] is True
    assert not any(issue['id'] == row['id'] for issue in audit(state)['issues'])


@pytest.mark.parametrize('kind', ['expense', 'income', 'opening_balance'])
def test_audit_does_not_unreview_explicit_reclassification_of_zero_import_hint(kind):
    state = state_for_pairs()
    state['categories']['confirmed'] = {'id': 'confirmed', 'name': kind, 'group': 'Finance', 'target': 0, 'kind': kind}
    row = state['transactions']['out-0']
    row.update(amount=0, categoryId='confirmed', inferredTransfer=True, isTransfer=kind == 'opening_balance', reviewed=True)
    before = deepcopy(state)
    assert review_status(row, state['categories'])['eligible']
    assert not any(issue['id'] == row['id'] for issue in audit(state)['issues'])
    with pytest.raises(StoreError, match='no invalid transfer'):
        repair_plan(state, [row['id']], apply=True)
    assert state == before


def test_valid_pair_can_receive_transfer_category_label_without_unmatching():
    from whab.domain.bulk import bulk_changes
    from whab.domain.transaction_changes import transaction_changes

    state = state_for_pairs()
    plan_many(state, pairs(1), reviewed=True, apply=True)
    before = deepcopy(state)
    bulk_changes(state, 'edit_transactions', [
        {'transactionId': 'out-0', 'categoryId': 'transfer', 'categoryAgent': 'Codex'},
        {'transactionId': 'in-0', 'categoryId': 'transfer', 'categoryAgent': 'Codex'},
    ], apply=True)
    assert all(row['categoryId'] == 'transfer' and row['reviewed'] for row in state['transactions'].values())
    assert all(valid_pair(row, state['transactions'], state['accounts'], state['categories']) for row in state['transactions'].values())
    transaction_changes(state, [{'transactionId': 'out-0', 'categoryId': None, 'reviewed': False}], [], apply=True)
    assert state['transactions']['out-0']['transferPeerId'] == 'in-0'
    assert state['transactions']['out-0']['isTransfer'] and not state['transactions']['out-0']['reviewed']
    for identifier, row in state['transactions'].items():
        for key in ('accountId', 'amount', 'date', 'merchant', 'note'):
            assert row[key] == before['transactions'][identifier][key]
    with pytest.raises(StoreError, match='Unmatch'):
        commands.edit_transaction(state, 'out-0', category_id='expense')


@pytest.mark.parametrize('kind', ['expense', 'income', 'opening_balance'])
def test_repair_preserves_explicit_nontransfer_classification_and_original_hint(kind):
    state = state_for_pairs()
    state['categories']['expense']['kind'] = kind
    row = state['transactions']['out-0']
    row.update(categoryId='expense', reviewed=True, transferPeerId='missing')
    before = deepcopy(row)
    preview = repair_plan(state, [row['id']])
    repair_plan(state, [row['id']], fingerprint=preview['fingerprint'], apply=True)
    repaired = state['transactions'][row['id']]
    assert repaired == {key: value for key, value in before.items() if key != 'transferPeerId'} | {'reviewed': False, 'isTransfer': False}
    assert repaired['inferredTransfer'] is True
    assert review_status(repaired, state['categories'], transactions=state['transactions'], accounts=state['accounts'])['eligible']
    assert not any(issue['id'] == row['id'] for issue in audit(state)['issues'])


@pytest.mark.parametrize('legacy_category', [None, 'transfer'])
def test_pair_undo_cannot_restore_orphan_transfer_label_but_unclassified_undo_is_safe(legacy_category):
    from whab.domain.history import apply_to_state, financial
    from whab.domain.ledger_state import portable_snapshot
    state = state_for_pairs()
    for category in state['categories'].values():
        category['currency'] = 'USD'
    for row in state['transactions'].values():
        row['merchantLogo'] = None
    state['transactions']['out-0']['categoryId'] = legacy_category
    original = deepcopy(state)
    wanted = financial(portable_snapshot(state))
    plan_many(state, pairs(1), apply=True)
    if legacy_category is not None:
        matched = deepcopy(state)
        with pytest.raises(StoreError, match='Transfer category without exactly two valid legs'):
            apply_to_state(state, wanted)
        assert state == matched
    else:
        apply_to_state(state, wanted)
        assert state['transactions'] == original['transactions']
        assert all(not row['reviewed'] and not row.get('transferPeerId') for row in state['transactions'].values())


@pytest.mark.parametrize('bulk', [False, True])
@pytest.mark.parametrize('reverse_ids', [False, True])
def test_existing_zero_pairs_require_explicit_ids_preserve_evidence_and_can_be_reviewed(bulk, reverse_ids):
    state = state_for_pairs()
    state['accounts']['other'].update(onBudget=False, closed=True, stale=True, balanceUnknown=True)
    for row in state['transactions'].values():
        row.update(amount=0, original={'importedMerchant': row['merchant'], 'importedAmount': 0})
    before = deepcopy(state)
    identifiers = ['in-0', 'out-0'] if reverse_ids else ['out-0', 'in-0']
    assert all(issue['code'] == 'zero_amount' for issue in audit(state)['issues'])
    assert candidates(state['accounts'], list(state['transactions'].values()), {}, categories=state['categories'])['total'] == 0
    if bulk:
        explicit = [{'outId': identifiers[0], 'inId': identifiers[1]}]
        preview = plan_many(state, explicit, reviewed=True)
        assert state == before
        result = plan_many(state, explicit, reviewed=True, fingerprint=preview['fingerprint'], apply=True)
    else:
        preview = plan(state, identifiers)
        assert state == before
        result = plan(state, identifiers, fingerprint=preview['fingerprint'], apply=True)
        assert all(not row['reviewed'] for row in state['transactions'].values())
    assert result == preview
    assert state['accounts'] == before['accounts'] and state['budget'] == before['budget']
    assert state['transactions'].keys() == before['transactions'].keys()
    assert [change['id'] for change in result['changes']] == identifiers
    for identifier, row in state['transactions'].items():
        assert valid_pair(row, state['transactions'], state['accounts'], state['categories'])
        assert {key: value for key, value in row.items() if key not in {'transferPeerId', 'reviewed'}} == {
            key: value for key, value in before['transactions'][identifier].items() if key != 'reviewed'}
        assert row['reviewed'] is bulk
        assert review_status(row, state['categories'], transactions=state['transactions'], accounts=state['accounts'])['eligible']
        commands.edit_transaction(state, identifier, category_id='transfer', reviewed=True)
    assert not audit(state)['issues']
    assert all(row['reviewed'] and row['amount'] == 0 for row in state['transactions'].values())
    summary = calculate_workspace(state, '2026-10')['summary']
    assert summary['transferFunding'] == summary['income'] == summary['spent'] == 0


def test_mixed_zero_and_nonzero_batch_preserves_unaddressed_rows_and_exact_fingerprint():
    state = state_for_pairs(3)
    for identifier in ['out-1', 'in-1']:
        state['transactions'][identifier]['amount'] = 0
    state['transactions']['out-2'].update(categoryId='expense', isTransfer=False, reviewed=True)
    before = deepcopy(state)
    explicit = pairs(2)
    preview = plan_many(state, explicit, reviewed=True)
    state['transactions']['in-1']['note'] += ' Later imported evidence'
    changed = deepcopy(state)
    with pytest.raises(StoreError, match='changed'):
        plan_many(state, explicit, reviewed=True, fingerprint=preview['fingerprint'], apply=True)
    assert state == changed
    state = deepcopy(before)
    plan_many(state, explicit, reviewed=True, fingerprint=preview['fingerprint'], apply=True)
    assert state['transactions'].keys() == before['transactions'].keys()
    assert state['transactions']['out-2'] == before['transactions']['out-2']
    assert state['transactions']['in-2'] == before['transactions']['in-2']
    assert state['accounts'] == before['accounts']
    assert all(state['transactions'][identifier]['reviewed'] for pair in explicit for identifier in pair.values())
    assert all(state['transactions'][identifier]['amount'] == 0 for identifier in ['out-1', 'in-1'])
    zero_receipt = [row for row in preview['changes'] if row['id'] in {'out-1', 'in-1'}]
    assert len(zero_receipt) == 2 and all(row['before']['amount'] == row['after']['amount'] == 0 for row in zero_receipt)
    confirmed = deepcopy(state)
    with pytest.raises(StoreError, match='already matched'):
        plan_many(state, explicit, reviewed=True, fingerprint=preview['fingerprint'], apply=True)
    assert state == confirmed


@pytest.mark.parametrize('invalid', ['one_nonzero', 'same_account', 'currency', 'pending', 'deleted', 'third_reference', 'duplicate'])
def test_invalid_explicit_zero_pair_rolls_back_entire_mixed_batch(invalid):
    state = state_for_pairs(3)
    for identifier in ['out-1', 'in-1']:
        state['transactions'][identifier]['amount'] = 0
    explicit = pairs(2)
    if invalid == 'one_nonzero':
        state['transactions']['in-1']['amount'] = -1
    elif invalid == 'same_account':
        state['transactions']['in-1']['accountId'] = 'cash'
    elif invalid == 'currency':
        state['accounts']['foreign'] = {**state['accounts']['other'], 'id': 'foreign', 'currency': 'EUR'}
        state['transactions']['in-1']['accountId'] = 'foreign'
    elif invalid in {'pending', 'deleted'}:
        state['transactions']['in-1'][invalid] = True
    elif invalid == 'third_reference':
        state['transactions']['out-2']['transferPeerId'] = 'in-1'
    else:
        explicit[1]['outId'] = 'out-0'
    before = deepcopy(state)
    with pytest.raises(StoreError):
        plan_many(state, explicit, reviewed=True, apply=True)
    assert state == before


def test_zero_pairs_stay_out_of_automatic_proposals_in_a_mixed_history():
    state = state_for_pairs(2)
    for identifier in ['out-1', 'in-1']:
        state['transactions'][identifier]['amount'] = 0
    result = candidates(state['accounts'], list(state['transactions'].values()), {}, categories=state['categories'])
    assert result['total'] == 1
    assert result['candidates'][0]['transactionIds'] == ['out-0', 'in-0']


def test_zero_pair_history_undo_redo_remain_guarded_and_change_no_money():
    from whab.domain.history import apply_to_state, financial
    from whab.domain.ledger_state import portable_snapshot
    state = state_for_pairs()
    for category in state['categories'].values():
        category['currency'] = 'USD'
    for row in state['transactions'].values():
        row.update(amount=0, merchantLogo=None)
    unpaired = financial(portable_snapshot(state))
    original_accounts = deepcopy(unpaired['accounts'])
    plan_many(state, pairs(1), reviewed=True, apply=True)
    paired = financial(portable_snapshot(state))
    apply_to_state(state, unpaired)
    assert all(not row['reviewed'] and not row.get('transferPeerId') for row in state['transactions'].values())
    apply_to_state(state, paired)
    assert all(row['reviewed'] and row['amount'] == 0 for row in state['transactions'].values())
    assert not audit(state)['issues'] and financial(portable_snapshot(state))['accounts'] == original_accounts
    assert calculate_workspace(state, '2026-10')['summary']['transferFunding'] == 0
    # The same new-orphan rule applies even though both amounts are zero.
    orphan = deepcopy(paired)
    orphan['transactions'][0].pop('transferPeerId')
    with pytest.raises(StoreError):
        apply_to_state(state, orphan)
    assert financial(portable_snapshot(state)) == paired
