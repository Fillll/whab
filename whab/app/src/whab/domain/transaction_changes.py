"""Explicit bounded financial corrections and creations, validated without I/O.

Existing annotation batches keep their older contract. This plan combines ordinary
core commands on detached records, calculates final account effects once, and only
publishes a complete valid plan. No split linkage or synthetic balancing entry is
stored: a cash-neutral split is simply an edit and a manual creation.
"""

from copy import deepcopy
from uuid import UUID

from . import commands
from .accounts import is_liability
from .errors import StoreError
from .money import cents, currency_code
from .transactions import public_transaction, validate_category_agent
from .validation import validate_date, validate_label

_FINANCIAL = {'accountId', 'date', 'merchant', 'amount'}
_ANNOTATIONS = {'categoryId', 'categoryAgent', 'reviewed', 'note', 'appendNote'}
_CREATE_REQUIRED = {'creationId', 'accountId', 'date', 'merchant', 'amount', 'currency'}
_CREATE_OPTIONAL = {'categoryId', 'categoryAgent', 'note'}


def _text(value, name, *, limit=200, empty=False):
    if type(value) is not str or not (0 if empty else 1) <= len(value) <= limit or '\x00' in value:
        raise StoreError(f'{name} must be valid text of at most {limit} characters.')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise StoreError(f'{name} must be valid UTF-8 text.') from None


def _fields(item):
    for key in ('transactionId', 'creationId', 'accountId'):
        if key in item:
            _text(item[key], key)
    if 'creationId' in item:
        try:
            if str(UUID(item['creationId'])) != item['creationId']:
                raise ValueError
        except ValueError:
            raise StoreError('creationId must be a canonical UUID; retain it for an exact retry.') from None
    if 'categoryId' in item and item['categoryId'] is not None:
        _text(item['categoryId'], 'categoryId')
    if 'categoryAgent' in item:
        validate_category_agent(item['categoryAgent'])
        if 'categoryId' not in item:
            raise StoreError('categoryAgent requires an explicit categoryId.')
    if 'reviewed' in item and type(item['reviewed']) is not bool:
        raise StoreError('reviewed must be true or false.')
    if 'note' in item:
        _text(item['note'], 'note', limit=1000, empty=True)
    if 'appendNote' in item:
        _text(item['appendNote'], 'appendNote', limit=1000)
        if 'note' in item:
            raise StoreError('Supply note or appendNote, not both.')
    if 'merchant' in item:
        _text(item['merchant'], 'merchant')
        validate_label(item['merchant'])
    if 'date' in item:
        validate_date(item['date'])
        if not '1900-01-01' <= item['date'] <= '2200-12-31':
            raise StoreError('Transaction date must be between 1900-01-01 and 2200-12-31.')
    if 'amount' in item and not cents(item['amount']):
        raise StoreError('Amount must not be zero.')
    if 'currency' in item:
        currency_code(item['currency'])


def validate_transaction_changes(edits, creates, *, preserve_balances=False):
    """Detach a 1–100-record intent without coercing fields or clearing omissions."""
    if type(preserve_balances) is not bool:
        raise StoreError('preserveBalances must be true or false.')
    if type(edits) is not list or type(creates) is not list or not 1 <= len(edits) + len(creates) <= 100:
        raise StoreError('An atomic transaction change must contain 1–100 edits and creations combined; it is never split.')
    seen = set()
    for action, items, identity in (('Edit', edits, 'transactionId'), ('Creation', creates, 'creationId')):
        for index, item in enumerate(items, 1):
            try:
                if type(item) is not dict:
                    raise StoreError('Expected an object.')
                if action == 'Edit':
                    allowed = _FINANCIAL | _ANNOTATIONS
                    if identity not in item or not item.keys() & allowed or item.keys() - allowed - {identity}:
                        raise StoreError('Supply transactionId and supported changes only.')
                elif not _CREATE_REQUIRED <= item.keys() or item.keys() - _CREATE_REQUIRED - _CREATE_OPTIONAL:
                    raise StoreError('Supply creationId, accountId, date, merchant, amount and currency; optional categoryId, categoryAgent and note only.')
                _fields(item)
                if item[identity] in seen:
                    raise StoreError('Duplicate transaction identity; an edit and creation cannot target the same record.')
                seen.add(item[identity])
            except StoreError as error:
                raise StoreError(f'{action} {index}: {error}') from None
    return deepcopy(edits), deepcopy(creates)


def _effect(account, amount):
    return amount if is_liability(account) else -amount


def _public(state, row):
    return {
        **public_transaction({**row, 'currency': state['accounts'][row['accountId']].get('currency', 'USD')}, categories=state['categories']),
        **{key: deepcopy(row[key]) for key in ('original', 'categoryEdited', 'deleted') if key in row},
    }


def transaction_changes(state, edits, creates, *, preserve_balances=False, apply=False):
    """Return an exact preview or publish every validated record and balance once."""
    edits, creates = validate_transaction_changes(edits, creates, preserve_balances=preserve_balances)
    changed = {}
    balance_deltas, cash_deltas, touched = {}, {}, {}
    changes = []

    def touch(identifier):
        account = state['accounts'].get(identifier)
        if account is None:
            raise StoreError('Account not found.')
        touched.setdefault(identifier, account)
        balance_deltas.setdefault(identifier, 0)
        cash_deltas.setdefault(identifier, 0)
        return account

    for index, edit in enumerate(edits, 1):
        identifier = edit['transactionId']
        before = state['transactions'].get(identifier)
        try:
            if before is None or before.get('deleted', False):
                raise StoreError('Transaction not found in this workspace or deleted.')
            if before.get('transferPeerId') and edit.keys() & {'accountId', 'date', 'amount', 'categoryId'}:
                raise StoreError('Unmatch this transfer pair before changing its financial fields or category.')
            old_account = touch(before['accountId'])
            new_account = touch(edit.get('accountId', before['accountId']))
            actual = {key: value for key, value in edit.items() if key in _FINANCIAL and value != before[key]}
            if actual and edit.get('reviewed') is True:
                raise StoreError('Financial corrections must remain unreviewed; omit reviewed or set it to false.')
            patch = {**actual, **{key: value for key, value in edit.items() if key in _ANNOTATIONS}}
            if 'appendNote' in patch:
                old_note = before['note']
                if type(old_note) is not str:
                    raise StoreError('Saved memo is invalid.')
                patch['note'] = old_note + ('\n' if old_note else '') + patch.pop('appendNote')
            if actual:
                patch['reviewed'] = False
            # Validate one ordinary correction without exposing transient batch
            # balances. Final effects are summed and range-checked below.
            accounts = {new_account['id']: {**new_account, 'balance': 0},
                        old_account['id']: {**old_account, 'balance': _effect(old_account, before['amount'])}}
            item_state = {**state, 'accounts': accounts, 'transactions': {identifier: deepcopy(before)}}
            if patch:
                if actual:
                    commands.edit_transaction_details(item_state, identifier, patch)
                else:
                    from .transactions import UNSET
                    commands.edit_transaction(item_state, identifier, patch.get('categoryId', UNSET),
                        patch.get('reviewed'), patch.get('note', UNSET), category_agent=patch.get('categoryAgent', UNSET))
            after = item_state['transactions'][identifier]
            if 'merchant' in actual:
                after['merchant'] = actual['merchant']
            if before['source'] in {'manual', 'demo'}:
                balance_deltas[old_account['id']] -= _effect(old_account, before['amount'])
                balance_deltas[new_account['id']] += _effect(new_account, after['amount'])
            cash_deltas[old_account['id']] -= before['amount']
            cash_deltas[new_account['id']] += after['amount']
            changed[identifier] = after
            changes.append({'id': identifier, 'before': _public(state, before), 'after': _public(state, after)})
        except (StoreError, KeyError, TypeError) as error:
            raise StoreError(f'Edit {index} ({identifier}): {error}') from None

    for index, creation in enumerate(creates, 1):
        identifier = creation['creationId']
        try:
            if identifier in state['transactions']:
                raise StoreError('Transaction identity already exists, including deleted records. Use the original request ID for an exact retry.')
            account = touch(creation['accountId'])
            if creation['currency'] != account.get('currency', 'USD'):
                raise StoreError('Transaction currency must match the account currency.')
            item_state = {**state, 'accounts': {account['id']: {**account, 'balance': 0}}, 'transactions': {}}
            commands.add_transaction(item_state, identifier, creation)
            after = item_state['transactions'][identifier]
            after['merchant'] = creation['merchant']
            if creation.get('categoryAgent') is not None:
                after['categoryAgent'] = creation['categoryAgent']
            balance_deltas[account['id']] += _effect(account, after['amount'])
            cash_deltas[account['id']] += after['amount']
            changed[identifier] = after
            changes.append({'id': identifier, 'before': None, 'after': _public(state, after)})
        except (StoreError, KeyError, TypeError) as error:
            raise StoreError(f'Creation {index} ({identifier}): {error}') from None

    if preserve_balances:
        nonzero = [identifier for identifier, delta in cash_deltas.items() if delta]
        if nonzero:
            raise StoreError('preserveBalances requires zero net transaction amount change in every account; nonzero accounts: ' + ', '.join(nonzero))
    balances = {identifier: cents(account['balance'] + (0 if preserve_balances else balance_deltas[identifier]))
                for identifier, account in touched.items()}
    result = {
        'count': len(changes), 'changed': sum(row['before'] != row['after'] for row in changes),
        'changes': changes, 'createdIds': [item['creationId'] for item in creates],
        'balanceChanges': [{'accountId': identifier, 'currency': account.get('currency', 'USD'),
                           'before': account['balance'], 'after': balances[identifier]}
                          for identifier, account in touched.items()],
    }
    if apply:
        state['transactions'].update(changed)
        for identifier, balance in balances.items():
            state['accounts'][identifier]['balance'] = balance
    return result
