"""Fictional financial batches conserve explicit splits and preserve source evidence."""

from copy import deepcopy
from uuid import NAMESPACE_URL, uuid5

import pytest

from whab.domain import commands
from whab.domain.bank_sync import merge_sync
from whab.domain.errors import StoreError
from whab.domain.history import REVERSIBLE
from whab.domain.imports import annotated_transaction
from whab.domain.ledger_state import initial_state
from whab.domain.money import MAX_CENTS
from whab.domain.transaction_changes import transaction_changes, validate_transaction_changes


def identifier(name):
    return str(uuid5(NAMESPACE_URL, 'whab-fictional-change/' + name))


def creation(**extra):
    return {'creationId': identifier('creation'), 'accountId': 'checking', 'date': '2026-05-02',
            'merchant': '  Fictional café 東京  ', 'amount': -25000, 'currency': 'USD',
            'categoryId': None, 'note': 'Exact new memo\n  ', **extra}


@pytest.fixture
def ledger():
    state = initial_state('personal', '2026-05')
    commands.add_account(state, 'checking', {'name': 'Fictional checking 🍏', 'type': 'depository', 'balance': 1000000})
    commands.add_account(state, 'other', {'name': 'Other fictional account', 'type': 'depository', 'balance': 10000})
    commands.add_account(state, 'credit', {'name': 'Fictional card', 'type': 'credit', 'balance': 50000})
    commands.add_account(state, 'euros', {'name': 'Fictional euro account', 'type': 'depository', 'currency': 'EUR', 'balance': 10000})
    state['categories']['transfers'] = {'id': 'transfers', 'name': 'Transfers', 'group': 'Finance',
                                       'currency': 'USD', 'target': 0, 'kind': 'transfer'}
    for key, amount in (('receipt', -100000), ('unrelated', 70000)):
        commands.add_transaction(state, key, {'accountId': 'checking', 'date': '2026-05-02',
            'merchant': 'Fictional receipt', 'amount': amount, 'categoryId': 'rent', 'note': ' [YNAB metadata]\nexact  '})
    state['transactions']['receipt'].update(source='ynab', reviewed=True, merchant='  Original merchant 🍏  ', categoryAgent='Earlier agent')
    state['accounts']['checking'].update(source='ynab', balanceUnknown=True, stale=True)
    return state


def test_imported_split_preview_and_commit_preserve_cash_and_identity(ledger):
    before = deepcopy(ledger)
    edits = [{'transactionId': 'receipt', 'amount': -75000}]
    creates = [creation()]
    plan = transaction_changes(ledger, edits, creates, preserve_balances=True)
    assert ledger == before
    assert plan['count'] == plan['changed'] == 2
    assert plan['createdIds'] == [creates[0]['creationId']]
    assert plan['changes'][1]['before'] is None
    assert plan['balanceChanges'] == [{'accountId': 'checking', 'currency': 'USD',
                                      'before': before['accounts']['checking']['balance'], 'after': before['accounts']['checking']['balance']}]
    saved = transaction_changes(ledger, edits, creates, preserve_balances=True, apply=True)
    assert saved == plan
    original = ledger['transactions']['receipt']
    assert original == {**before['transactions']['receipt'], 'amount': -75000, 'reviewed': False, 'original': {'amount': -100000}}
    new = ledger['transactions'][creates[0]['creationId']]
    assert new['source'] == 'manual' and not new['reviewed'] and not new['pending']
    assert new['merchant'] == creates[0]['merchant'] and new['note'] == creates[0]['note']
    assert new['categoryId'] is None and not new['isTransfer']
    assert original['amount'] + new['amount'] == before['transactions']['receipt']['amount']
    assert ledger['accounts'] == before['accounts']
    assert ledger['transactions']['unrelated'] == before['transactions']['unrelated']
    assert ledger['categories'] == before['categories']
    assert 'change_transactions' in REVERSIBLE


def test_imported_override_preserves_latest_provider_evidence_on_refresh(ledger):
    transaction_changes(ledger, [{'transactionId': 'receipt', 'amount': -75000}], [creation()], preserve_balances=True, apply=True)
    corrected = ledger['transactions']['receipt']
    provider_record = {'id': 'receipt', 'accountId': 'checking', 'date': '2026-05-02',
        'merchant': '  Latest provider merchant  ', 'amount': -110000, 'suggestedCategoryId': 'shopping',
        'pending': False, 'source': 'ynab', 'inferredTransfer': False, 'pendingTransactionId': None}
    refreshed = annotated_transaction(provider_record, corrected, categories=ledger['categories'])
    assert refreshed['amount'] == -75000 and refreshed['original']['amount'] == -110000
    assert refreshed['note'] == corrected['note'] and refreshed['categoryId'] == 'rent'
    assert not refreshed['reviewed']
    assert ledger['transactions'][identifier('creation')]['source'] == 'manual'


def test_split_survives_provider_refresh_and_pending_posting_without_changing_manual_record():
    state = initial_state('personal', '2026-05')
    state['items']['fictional-item'] = {'id': 'fictional-item', 'provider': 'plaid',
                                      'token': 'fictional-ciphertext', 'cursor': None}
    account = {'id': 'checking', 'name': 'Fictional linked checking', 'type': 'depository',
               'balance': 1000000, 'currency': 'USD', 'source': 'plaid',
               'itemId': 'fictional-item', 'institution': 'Fictional Bank'}
    pending = {'id': 'pending-receipt', 'accountId': 'checking', 'date': '2026-05-02',
               'merchant': '  Fictional bank merchant  ', 'amount': -100000,
               'suggestedCategoryId': 'rent', 'pending': True, 'source': 'plaid',
               'inferredTransfer': False, 'pendingTransactionId': None}
    merge_sync(state, 'fictional-item', {'added': [pending], 'modified': [], 'removed': []},
               'first', [account], synced_at='2026-05-02T00:00:00Z')
    commands.edit_transaction(state, 'pending-receipt', 'rent', None, ' Imported note\n  ', category_agent='Codex')
    transaction_changes(state, [{'transactionId': 'pending-receipt', 'amount': -75000}],
                        [creation(categoryId='rent')], preserve_balances=True, apply=True)
    manual = deepcopy(state['transactions'][identifier('creation')])
    refreshed = {**pending, 'amount': -105000, 'merchant': '  Refreshed provider merchant  '}
    merge_sync(state, 'fictional-item', {'added': [], 'modified': [refreshed], 'removed': []},
               'second', [{**account, 'balance': 995000}], synced_at='2026-05-03T00:00:00Z')
    corrected = state['transactions']['pending-receipt']
    assert corrected['amount'] == -75000 and corrected['original'] == {'amount': -105000}
    assert corrected['merchant'] == refreshed['merchant']
    assert state['transactions'][identifier('creation')] == manual
    posted = {**refreshed, 'id': 'posted-receipt', 'amount': -110000, 'pending': False,
              'date': '2026-05-04', 'pendingTransactionId': 'pending-receipt'}
    merge_sync(state, 'fictional-item', {'added': [posted], 'modified': [], 'removed': ['pending-receipt']},
               'third', [{**account, 'balance': 990000}], synced_at='2026-05-04T00:00:00Z')
    corrected = state['transactions']['posted-receipt']
    assert corrected['amount'] == -75000 and corrected['original'] == {'amount': -110000}
    assert corrected['date'] == posted['date'] and corrected['source'] == 'plaid'
    assert corrected['categoryId'] == 'rent' and corrected['categoryAgent'] == 'Codex'
    assert corrected['note'] == ' Imported note\n  ' and not corrected['reviewed']
    assert not corrected['pending'] and state['transactions']['pending-receipt']['deleted']
    assert state['transactions'][identifier('creation')] == manual
    assert state['accounts']['checking']['balance'] == 990000


def test_ordinary_create_uses_manual_balance_effects_and_exact_text(ledger):
    before = deepcopy(ledger)
    creates = [creation(categoryAgent='Codex'), creation(creationId=identifier('credit'), accountId='credit', amount=1234)]
    result = transaction_changes(ledger, [], creates, apply=True)
    assert ledger['accounts']['checking']['balance'] == before['accounts']['checking']['balance'] + 25000
    assert ledger['accounts']['credit']['balance'] == before['accounts']['credit']['balance'] + 1234
    assert result['changes'][0]['after']['categoryAgent'] == 'Codex'
    assert all(not row['after']['reviewed'] and row['after']['source'] == 'manual' for row in result['changes'])
    assert ledger['transactions']['receipt'] == before['transactions']['receipt']


def test_manual_edit_and_move_accumulate_account_effects_once(ledger):
    before = deepcopy(ledger)
    edits = [{'transactionId': 'unrelated', 'accountId': 'credit', 'amount': 60000, 'merchant': '  Corrected payee  ', 'date': '2026-05-03'}]
    result = transaction_changes(ledger, edits, [], apply=True)
    assert ledger['accounts']['checking']['balance'] == before['accounts']['checking']['balance'] + 70000
    assert ledger['accounts']['credit']['balance'] == before['accounts']['credit']['balance'] + 60000
    assert ledger['transactions']['unrelated']['merchant'] == '  Corrected payee  '
    assert not ledger['transactions']['unrelated']['reviewed']
    assert 'original' not in ledger['transactions']['unrelated']
    assert result['balanceChanges'][0]['accountId'] == 'checking'
    assert result['balanceChanges'][1]['accountId'] == 'credit'


def test_ordinary_imported_correction_keeps_snapshot_balance_and_prior_original(ledger):
    ledger['transactions']['receipt']['original'] = {'merchant': '  Provider name  '}
    before = deepcopy(ledger)
    transaction_changes(ledger, [{'transactionId': 'receipt', 'amount': -75000}], [], apply=True)
    assert ledger['accounts'] == before['accounts']
    assert ledger['transactions']['receipt']['original'] == {'merchant': '  Provider name  ', 'amount': -100000}
    assert ledger['transactions']['receipt']['merchant'] == before['transactions']['receipt']['merchant']


def test_annotation_batches_and_noop_financial_fields_keep_review_and_original(ledger):
    before = deepcopy(ledger)
    row = before['transactions']['receipt']
    edits = [{'transactionId': 'receipt', **{key: row[key] for key in ('amount', 'merchant', 'date', 'accountId')}}]
    result = transaction_changes(ledger, edits, [], apply=True)
    assert result['changed'] == 0 and ledger == before
    transaction_changes(ledger, [{'transactionId': 'receipt', 'appendNote': '  exact appended\n'}], [], apply=True)
    assert ledger['transactions']['receipt'] == {**row, 'note': row['note'] + '\n  exact appended\n'}
    transaction_changes(ledger, [{'transactionId': 'receipt', 'categoryId': None}], [], apply=True)
    assert not ledger['transactions']['receipt']['reviewed']
    assert 'categoryAgent' not in ledger['transactions']['receipt']


def test_combined_financial_and_category_correction_validates_final_account_pair(ledger):
    edits = [{'transactionId': 'receipt', 'amount': -75000, 'categoryId': 'transfers', 'categoryAgent': 'Codex', 'note': ''}]
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='exactly two'):
        transaction_changes(ledger, edits, [], apply=True)
    assert ledger == before
    edits[0]['categoryId'] = 'groceries'
    transaction_changes(ledger, edits, [], apply=True)
    row = ledger['transactions']['receipt']
    assert row['categoryId'] == 'groceries' and row['categoryAgent'] == 'Codex' and not row['isTransfer']
    assert row['note'] == '' and not row['reviewed']


def test_final_balance_validation_has_no_intermediate_overflow(ledger):
    ledger['accounts']['checking']['balance'] = MAX_CENTS
    ledger['transactions']['unrelated']['amount'] = MAX_CENTS
    edits = [{'transactionId': 'unrelated', 'amount': -MAX_CENTS}]
    creates = [creation(amount=MAX_CENTS), creation(creationId=identifier('second'), amount=MAX_CENTS)]
    transaction_changes(ledger, edits, creates, apply=True)
    assert ledger['accounts']['checking']['balance'] == MAX_CENTS
    imported = deepcopy(ledger)
    imported['transactions']['unrelated'].update(source='ynab', amount=MAX_CENTS)
    transaction_changes(imported, edits, creates=[creation(creationId=identifier('third'), amount=MAX_CENTS),
        creation(creationId=identifier('fourth'), amount=MAX_CENTS)], preserve_balances=True, apply=True)
    assert imported['accounts']['checking']['balance'] == MAX_CENTS


def test_preserve_balances_requires_conservation_per_account_not_just_currency(ledger):
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='every account'):
        transaction_changes(ledger, [{'transactionId': 'receipt', 'amount': -75000}], [creation(accountId='other')], preserve_balances=True, apply=True)
    assert ledger == before
    with pytest.raises(StoreError, match='every account'):
        transaction_changes(ledger, [], [creation()], preserve_balances=True, apply=True)
    assert ledger == before


@pytest.mark.parametrize('bad', [
    {'transactionId': 'absent', 'note': 'bad'},
    {'transactionId': 'receipt', 'accountId': 'absent'},
    {'transactionId': 'receipt', 'accountId': 'euros'},
    {'transactionId': 'receipt', 'categoryId': 'absent'},
    {'transactionId': 'receipt', 'amount': -75000, 'reviewed': True},
    {'transactionId': 'receipt', 'appendNote': 'x' * 1000},
])
def test_invalid_later_edit_never_publishes_earlier_change(ledger, bad):
    before = deepcopy(ledger)
    with pytest.raises(StoreError):
        transaction_changes(ledger, [{'transactionId': 'unrelated', 'note': 'valid first'} , bad], [creation()], apply=True)
    assert ledger == before


@pytest.mark.parametrize('change', [{'accountId': 'absent'}, {'currency': 'EUR'}, {'categoryId': 'absent'}])
def test_invalid_creation_never_publishes_edit(ledger, change):
    before = deepcopy(ledger)
    with pytest.raises(StoreError):
        transaction_changes(ledger, [{'transactionId': 'receipt', 'note': 'valid first'}], [creation(**change)], apply=True)
    assert ledger == before


def test_deleted_targets_and_creation_collision_are_rejected(ledger):
    ledger['transactions']['receipt']['deleted'] = True
    duplicate = identifier('creation')
    ledger['transactions'][duplicate] = {**ledger['transactions']['receipt'], 'id': duplicate}
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='deleted'):
        transaction_changes(ledger, [{'transactionId': 'receipt', 'note': 'x'}], [], apply=True)
    with pytest.raises(StoreError, match='already exists'):
        transaction_changes(ledger, [], [creation()], apply=True)
    assert ledger == before


def test_closed_accounts_and_matched_transfer_protections(ledger):
    ledger['accounts']['other']['closed'] = True
    ledger['transactions']['receipt']['transferPeerId'] = 'unrelated'
    before = deepcopy(ledger)
    for edit in ({'transactionId': 'receipt', 'amount': -100000}, {'transactionId': 'unrelated', 'accountId': 'other'}):
        with pytest.raises(StoreError):
            transaction_changes(ledger, [edit], [], apply=True)
        assert ledger == before
    with pytest.raises(StoreError, match='archived'):
        transaction_changes(ledger, [], [creation(accountId='other')], apply=True)
    assert ledger == before
    transaction_changes(ledger, [{'transactionId': 'receipt', 'note': 'Annotation still allowed'}], [], apply=True)
    assert ledger['transactions']['receipt']['transferPeerId'] == 'unrelated'


def test_invalid_final_balance_refuses_entire_batch(ledger):
    ledger['accounts']['checking']['balance'] = MAX_CENTS
    before = deepcopy(ledger)
    with pytest.raises(StoreError, match='Money'):
        transaction_changes(ledger, [{'transactionId': 'receipt', 'note': 'valid'}], [creation()], apply=True)
    assert ledger == before


@pytest.mark.parametrize('edits,creates,extra', [
    ([], [], {}), (None, [], {}), ([], {}, {}),
    ([{'transactionId': 'receipt', 'note': 'x'}] * 101, [], {}),
    ([{'transactionId': 'receipt', 'note': 'x'}], [], {'preserve_balances': 1}),
    ([None], [], {}), ([{'transactionId': 'receipt'}], [], {}),
    ([{'transactionId': 'receipt', 'source': 'manual'}], [], {}),
    ([{'transactionId': 'receipt', 'currency': 'USD'}], [], {}),
    ([{'transactionId': 'receipt', 'note': 'x'}, {'transactionId': 'receipt', 'note': 'y'}], [], {}),
    ([{'transactionId': 'receipt', 'reviewed': None}], [], {}),
    ([{'transactionId': 'receipt', 'note': 'x', 'appendNote': 'y'}], [], {}),
    ([{'transactionId': 'receipt', 'appendNote': ''}], [], {}),
    ([{'transactionId': 'receipt', 'note': '\x00'}], [], {}),
    ([{'transactionId': 'receipt', 'note': '\ud800'}], [], {}),
    ([{'transactionId': '', 'note': 'x'}], [], {}),
    ([{'transactionId': 'receipt', 'amount': True}], [], {}),
    ([{'transactionId': 'receipt', 'amount': 0}], [], {}),
    ([{'transactionId': 'receipt', 'date': '2026-02-30'}], [], {}),
    ([{'transactionId': 'receipt', 'date': '1899-12-31'}], [], {}),
    ([{'transactionId': 'receipt', 'merchant': '  '}], [], {}),
    ([{'transactionId': 'receipt', 'categoryAgent': 'Codex'}], [], {}),
    ([{'transactionId': 'receipt', 'categoryId': None, 'categoryAgent': ' bad '}], [], {}),
    ([{'transactionId': 'receipt', 'categoryId': 5}], [], {}),
    ([], [creation(creationId='not-a-uuid')], {}),
    ([], [creation(creationId=identifier('creation').upper())], {}),
    ([], [creation(), creation()], {}),
    ([], [creation(source='manual')], {}),
    ([], [creation(reviewed=True)], {}),
    ([], [creation(currency='usd')], {}),
    ([], [{'creationId': identifier('creation')}], {}),
    ([{'transactionId': identifier('creation'), 'note': 'x'}], [creation()], {}),
])
def test_strict_intent_validation(edits, creates, extra):
    with pytest.raises(StoreError):
        validate_transaction_changes(edits, creates, **extra)


def test_validation_detaches_and_does_not_normalize_explicit_text():
    edits = [{'transactionId': 'receipt', 'note': '  Exact  ', 'categoryId': None}]
    creates = [creation()]
    actual_edits, actual_creates = validate_transaction_changes(edits, creates)
    assert (actual_edits, actual_creates) == (edits, creates)
    actual_edits[0]['note'] = 'different'
    actual_creates[0]['merchant'] = 'different'
    assert edits[0]['note'] == '  Exact  ' and creates[0]['merchant'] == '  Fictional café 東京  '
