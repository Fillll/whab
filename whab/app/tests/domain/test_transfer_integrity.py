"""Fictional strict pairs, atomic plans, legacy repair and funding boundaries."""

from copy import deepcopy

import pytest

from whab.domain import commands
from whab.domain.budget import calculate_workspace
from whab.domain.categories import classify_category
from whab.domain.errors import StoreError
from whab.domain.ledger_state import initial_state
from whab.domain.review import review_status
from whab.domain.transfer_matching import (
    audit,
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


def test_zero_value_hint_is_explicitly_unreviewed_and_repair_preserves_evidence():
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
    with pytest.raises(StoreError, match='Zero-value'):
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
