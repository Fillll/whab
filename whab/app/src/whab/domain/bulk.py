"""Bounded atomic annotation/classification plans, independent of persistence."""

from copy import deepcopy

from . import commands
from .accounts import is_on_budget
from .errors import StoreError
from .transactions import UNSET, public_transaction, validate_category_agent

OPERATIONS = {'edit_transactions': 'transactionId', 'classify_accounts': 'accountId'}


def validate_edits(operation, edits):
    """Detach one explicit intent; never coerce IDs, booleans or clear values."""
    if operation not in OPERATIONS:
        raise StoreError('Choose edit_transactions or classify_accounts.')
    if type(edits) is not list or not 1 <= len(edits) <= 100:
        raise StoreError('An atomic batch must contain 1–100 edits; it is never split.')
    identity = OPERATIONS[operation]
    allowed = ({'categoryId', 'categoryAgent', 'reviewed', 'note', 'appendNote'}
               if operation == 'edit_transactions' else {'type', 'subtype', 'onBudget'})
    seen = set()
    for index, edit in enumerate(edits, 1):
        if type(edit) is not dict or identity not in edit or not edit.keys() & allowed or edit.keys() - allowed - {identity}:
            raise StoreError(f'Item {index}: supply {identity} and supported changes only.')
        value = edit[identity]
        if type(value) is not str or not 1 <= len(value) <= 200 or '\x00' in value:
            raise StoreError(f'Item {index}: invalid {identity}.')
        try:
            value.encode('utf-8')
        except UnicodeError:
            raise StoreError(f'Item {index}: invalid UTF-8 identity.') from None
        if value in seen:
            raise StoreError(f'Item {index}: duplicate {identity} {value!r}; nothing was changed.')
        seen.add(value)
        if 'reviewed' in edit and type(edit['reviewed']) is not bool:
            raise StoreError(f'Item {index}: reviewed must be true or false, not null.')
        if 'onBudget' in edit and type(edit['onBudget']) is not bool:
            raise StoreError(f'Item {index}: onBudget must be true or false, not null.')
        if 'categoryId' in edit and edit['categoryId'] is not None and (
            type(edit['categoryId']) is not str or not 1 <= len(edit['categoryId']) <= 200 or '\x00' in edit['categoryId']
        ):
            raise StoreError(f'Item {index}: categoryId must be a stable ID or null to clear.')
        if 'categoryAgent' in edit:
            validate_category_agent(edit['categoryAgent'])
            if 'categoryId' not in edit:
                raise StoreError(f'Item {index}: categoryAgent requires an explicit categoryId change.')
        if 'note' in edit and (type(edit['note']) is not str or len(edit['note']) > 1000):
            raise StoreError(f'Item {index}: note must be text of at most 1000 characters; use "" to clear.')
        if 'appendNote' in edit and ('note' in edit or type(edit['appendNote']) is not str or not 1 <= len(edit['appendNote']) <= 1000):
            raise StoreError(f'Item {index}: appendNote needs 1–1000 characters and cannot accompany note.')
    return deepcopy(edits)


def bulk_changes(state, operation, edits, *, apply=False):
    """Validate all candidates before publishing ANY record into the supplied state."""
    edits = validate_edits(operation, edits)
    collection = 'transactions' if operation == 'edit_transactions' else 'accounts'
    identity = OPERATIONS[operation]
    candidate = {**state, collection: {}}
    changes = []
    for index, edit in enumerate(edits, 1):
        identifier = edit[identity]
        previous = state[collection].get(identifier)
        if previous is None or previous.get('deleted', False):
            raise StoreError(f'Item {index} ({identifier}): {collection[:-1]} not found in this workspace or deleted.')
        candidate[collection][identifier] = deepcopy(previous)
        try:
            if operation == 'edit_transactions':
                note = edit.get('note', UNSET)
                if 'appendNote' in edit:
                    old = previous['note']
                    if type(old) is not str:
                        raise StoreError('Saved memo is invalid.')
                    note = old + ('\n' if old else '') + edit['appendNote']
                commands.edit_transaction(candidate, identifier, edit.get('categoryId', UNSET),
                    edit.get('reviewed'), note, category_agent=edit.get('categoryAgent', UNSET))
            else:
                commands.update_account(candidate, identifier, {
                    'onBudget': is_on_budget(previous), **{key: value for key, value in edit.items() if key != identity},
                })
        except (StoreError, KeyError, TypeError) as error:
            raise StoreError(f'Item {index} ({identifier}): {error}') from None
        after = candidate[collection][identifier]
        def public(row):
            if operation == 'edit_transactions':
                return public_transaction({**row, 'currency': state['accounts'][row['accountId']].get('currency', 'USD')})
            return deepcopy(row)
        changes.append({'id': identifier, 'before': public(previous), 'after': public(after)})
    if apply:
        state[collection].update(candidate[collection])
    return {'count': len(changes), 'changed': sum(row['before'] != row['after'] for row in changes), 'changes': changes}
