"""Explicit reversible exclusion of existing records, without fabricated money."""

from copy import deepcopy

from .accounts import is_liability
from .errors import StoreError
from .history import digest
from .money import cents
from .portable import TRANSACTION_FIELDS
from .transfer_matching import archived_pair, incoming_links, valid_pair


def validate_void_ids(transaction_ids):
    """Detach one bounded explicit intent; never infer or split its targets."""
    if type(transaction_ids) is not list or not 1 <= len(transaction_ids) <= 100:
        raise StoreError('A void or unvoid batch must contain 1–100 explicit transaction IDs; it is never split.')
    seen = set()
    for index, identifier in enumerate(transaction_ids, 1):
        if type(identifier) is not str or not 1 <= len(identifier) <= 200 or '\x00' in identifier:
            raise StoreError(f'Transaction {index}: supply a nonempty stable ID of at most 200 characters.')
        try:
            identifier.encode('utf-8')
        except UnicodeError:
            raise StoreError(f'Transaction {index}: the ID must contain valid UTF-8 text.') from None
        if identifier in seen:
            raise StoreError(f'Transaction {index}: duplicate transaction ID {identifier!r}.')
        seen.add(identifier)
    return list(transaction_ids)


def _public(state, row):
    account = state['accounts'][row['accountId']]
    fields = TRANSACTION_FIELDS | {'original', 'categoryAgent', 'transferPeerId', 'voided', 'providerDeleted'}
    return {**{key: deepcopy(value) for key, value in row.items() if key in fields},
            'currency': account.get('currency', 'USD'), 'accountName': account['name']}


def plan(state, transaction_ids, *, action='void', fingerprint=None, apply=False):
    """Validate every exclusion/restoration and publish all rows and balances once.

    Imported snapshots are unchanged. Manual/demo entries reverse or restore their
    normal cash/liability balance effect. Explicit unvoid never restores review.
    """
    transaction_ids = validate_void_ids(transaction_ids)
    if type(action) is not str or action not in {'void', 'unvoid'}:
        raise StoreError('Choose void or unvoid.')
    if apply and (type(fingerprint) is not str or len(fingerprint) != 64
                  or any(character not in '0123456789abcdef' for character in fingerprint)):
        raise StoreError('Applying void or unvoid requires the exact 64-character fingerprint from its preview.')
    incoming = incoming_links(state['transactions'])
    selected = set(transaction_ids)
    updates, touched, deltas, changes = {}, {}, {}, []
    for identifier in transaction_ids:
        row = state['transactions'].get(identifier)
        if row is None:
            raise StoreError(f'{identifier}: transaction not found in this workspace.')
        account = state['accounts'].get(row.get('accountId'))
        if account is None:
            raise StoreError(f'{identifier}: the saved account is missing; repair the ledger before changing it.')
        if action == 'void' and row.get('deleted'):
            raise StoreError(f'{identifier}: already deleted or voided; use the original request ID for an exact retry.')
        if action == 'unvoid' and (row.get('voided') is not True or row.get('deleted') is not True):
            raise StoreError(f'{identifier}: only an explicitly voided transaction can be unvoided.')
        if row.get('providerDeleted'):
            raise StoreError(f'{identifier}: the provider removed this transaction; unvoid is unavailable until the provider restores it.')
        linked = incoming.get(identifier, set()) | ({row['transferPeerId']} if row.get('transferPeerId') else set())
        if linked - selected:
            raise StoreError(f'{identifier}: include every linked transfer counterpart explicitly: ' + ', '.join(sorted(linked - selected)))
        proof = valid_pair if action == 'void' else archived_pair
        if linked and not proof(row, state['transactions'], state['accounts'], state['categories'], incoming=incoming):
            raise StoreError(f'{identifier}: invalid transfer pair; repair its links before voiding or unvoiding.')
        if not linked and state['categories'].get(row.get('categoryId'), {}).get('kind') == 'transfer':
            raise StoreError(f'{identifier}: a Transfer category requires a complete pair; repair the classification first.')
        after = deepcopy(row)
        after.update(deleted=action == 'void', reviewed=False)
        # Retain false as an explicit restoration decision. A later posted
        # refresh must not re-inherit a pending predecessor's old tombstone.
        after['voided'] = action == 'void'
        updates[identifier] = after
        touched.setdefault(account['id'], account)
        delta = (row['amount'] if is_liability(account) else -row['amount']) if row['source'] in {'manual', 'demo'} else 0
        deltas[account['id']] = deltas.get(account['id'], 0) + (-delta if action == 'void' else delta)
        changes.append({'id': identifier, 'before': _public(state, row), 'after': _public(state, after)})
    balances = {}
    for identifier, account in touched.items():
        if deltas[identifier] and account['balance'] is None:
            raise StoreError(f'{identifier}: a manual-entry balance adjustment needs a known numeric balance; no transactions were changed.')
        balances[identifier] = account['balance'] if not deltas[identifier] else cents(account['balance'] + deltas[identifier])
    # Check the complete candidate before any state is published. This also
    # prevents an unrelated incoming reference from becoming a surviving orphan.
    candidate = {**state['transactions'], **updates}
    for identifier, row in updates.items():
        if row.get('transferPeerId'):
            proof = archived_pair if action == 'void' else valid_pair
            if not proof(row, candidate, state['accounts'], state['categories'], incoming=incoming):
                raise StoreError(f'{identifier}: the complete proposed transfer pair is invalid; nothing was changed.')
    balance_changes = [{'accountId': identifier, 'currency': account.get('currency', 'USD'),
                        'accountType': account['type'], 'before': account['balance'], 'after': balances[identifier]}
                       for identifier, account in touched.items()]
    observed = digest([action, changes, balance_changes, list(touched.values()),
                       [state['categories'].get(state['transactions'][identifier].get('categoryId')) for identifier in transaction_ids]])
    if fingerprint is not None and fingerprint != observed:
        raise StoreError('Void proposal changed. Preview the exact selection again; nothing was changed.')
    if apply:
        state['transactions'].update(updates)
        for identifier, balance in balances.items():
            state['accounts'][identifier]['balance'] = balance
    return {'action': action, 'count': len(changes), 'changed': len(changes), 'fingerprint': observed,
            'changes': changes, 'balanceChanges': balance_changes}
