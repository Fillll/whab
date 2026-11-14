"""Deterministic transfer proposals and explicitly confirmed, reversible pairs."""

from bisect import bisect_left, bisect_right
from collections import defaultdict
from copy import deepcopy
from datetime import date
from hashlib import sha256
from json import dumps

from .accounts import is_on_budget
from .errors import StoreError
from .transactions import public_transaction
from .validation import validate_date

MAX_DAYS = 7
MAX_CANDIDATES = 1000


def _compatible(left, right, accounts, categories=None):
    a, b = accounts[left['accountId']], accounts[right['accountId']]
    return (left['id'] != right['id'] and a['id'] != b['id']
            and a.get('currency', 'USD') == b.get('currency', 'USD')
            and is_on_budget(a) == is_on_budget(b)
            and left['amount'] != 0 and left['amount'] == -right['amount']
            and not any(row.get('deleted') or row.get('pending') or row.get('categoryId') is not None and (categories or {}).get(row['categoryId'], {}).get('kind') != 'transfer' for row in (left, right)))


def validate_matches(state):
    """Pair identities cannot dangle or conceal a cross-currency/scope imbalance."""
    for row in state['transactions'].values():
        if 'transferPeerId' not in row:
            continue
        peer_id = row['transferPeerId']
        peer = state['transactions'].get(peer_id) if type(peer_id) is str else None
        if (peer is None or peer.get('transferPeerId') != row['id']
                or not _compatible(row, peer, state['accounts'], state.get('categories'))
                or not row.get('isTransfer') or not peer.get('isTransfer')):
            raise StoreError('Invalid transfer pair. Unmatch it before changing its amount, account, category or budget scope.')


def reconcile_matches(state):
    """Provider corrections/removals invalidate both links and require fresh review."""
    invalid = set()
    for row in state['transactions'].values():
        peer = state['transactions'].get(row.get('transferPeerId'))
        if 'transferPeerId' in row and (peer is None or peer.get('transferPeerId') != row['id']
                                       or not _compatible(row, peer, state['accounts'], state.get('categories'))):
            invalid.add(row['id'])
            if peer is not None:
                invalid.add(peer['id'])
    for identifier in invalid:
        row = state['transactions'][identifier]
        row.pop('transferPeerId', None)
        row['isTransfer'] = row.get('inferredTransfer', False) or state.get('categories', {}).get(row.get('categoryId'), {}).get('kind') == 'transfer'
        row['reviewed'] = False


def plan(state, transaction_ids, *, action='match', fingerprint=None, apply=False):
    if (type(transaction_ids) is not list or len(transaction_ids) != 2
            or any(type(value) is not str or not 1 <= len(value) <= 200 for value in transaction_ids)
            or len(set(transaction_ids)) != 2 or type(action) is not str or action not in {'match', 'unmatch'}):
        raise StoreError('Choose exactly two distinct transaction IDs and match or unmatch.')
    rows = [state['transactions'].get(identifier) for identifier in transaction_ids]
    if any(row is None or row.get('deleted') for row in rows):
        raise StoreError('A transfer transaction is missing or deleted in this workspace.')
    left, right = rows
    accounts = state['accounts']
    if action == 'match':
        if any(row.get('transferPeerId') for row in rows):
            raise StoreError('A transaction is already matched. Inspect or unmatch its existing pair first.')
        if not _compatible(left, right, accounts, state.get('categories')):
            raise StoreError('A transfer needs posted, uncategorized, opposite equal amounts in different accounts of the same currency and budget scope.')
        if any(accounts[row['accountId']].get('closed', False) for row in rows):
            raise StoreError('Archived accounts cannot receive new transfer matches.')
        if abs((date.fromisoformat(left['date']) - date.fromisoformat(right['date'])).days) > MAX_DAYS:
            raise StoreError('Transfer dates must be within seven calendar days.')
    elif left.get('transferPeerId') != right['id'] or right.get('transferPeerId') != left['id']:
        raise StoreError('These transactions are not a confirmed pair. Refresh the matched transactions.')
    observed = sha256(dumps([action, rows, [accounts[row['accountId']] for row in rows]], sort_keys=True).encode()).hexdigest()
    if fingerprint is not None and fingerprint != observed:
        raise StoreError('Transfer proposal changed. Preview and review the current pair; nothing was changed.')
    changes = []
    for row, peer in ((left, right), (right, left)):
        after = deepcopy(row)
        after['reviewed'] = False
        if action == 'match':
            after.update(transferPeerId=peer['id'], isTransfer=True)
        else:
            after.pop('transferPeerId', None)
            after['isTransfer'] = after.get('inferredTransfer', False) or state.get('categories', {}).get(after.get('categoryId'), {}).get('kind') == 'transfer'
        currency = accounts[row['accountId']].get('currency', 'USD')
        changes.append({'id': row['id'], 'before': {**public_transaction({**row, 'currency': currency}), 'accountName': accounts[row['accountId']]['name']},
                        'after': {**public_transaction({**after, 'currency': currency}), 'accountName': accounts[row['accountId']]['name']}})
        if apply:
            state['transactions'][row['id']] = after
    return {'action': action, 'fingerprint': observed, 'count': 2, 'changes': changes}


def candidates(accounts, transactions, parameters, *, categories=None):
    """Read-only proposals; alternatives stay explicit and never reserve a record."""
    if type(parameters) is not dict or parameters.keys() - {'since', 'until', 'currency', 'accountIds', 'offset', 'limit'}:
        raise StoreError('Invalid transfer proposal filters.')
    for key in ('since', 'until'):
        if parameters.get(key) is not None:
            validate_date(parameters[key])
    if parameters.get('since') and parameters.get('until') and parameters['since'] > parameters['until']:
        raise StoreError('Transfer since must not be after until.')
    offset, limit = parameters.get('offset', 0), parameters.get('limit', 100)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise StoreError('Use a nonnegative offset and a limit of 1–100 proposals.')
    selected = parameters.get('accountIds', [])
    if type(selected) is not list or any(type(value) is not str or value not in accounts for value in selected):
        raise StoreError('Choose account IDs belonging to this budget.')
    currency = parameters.get('currency')
    if currency is not None:
        from .money import currency_code
        currency = currency_code(currency)
    def in_scope(row):
        return ((not selected or row['accountId'] in selected)
                and (not parameters.get('since') or row['date'] >= parameters['since'])
                and (not parameters.get('until') or row['date'] <= parameters['until']))
    eligible, inflows = [], defaultdict(list)
    for row in transactions:
        account = accounts.get(row['accountId'])
        if (not account or account.get('closed') or row.get('deleted') or row.get('pending')
                or row.get('categoryId') is not None and (categories or {}).get(row['categoryId'], {}).get('kind') != 'transfer' or row.get('transferPeerId') or not row['amount']
                or currency is not None and account.get('currency', 'USD') != currency):
            continue
        ordinal = date.fromisoformat(row['date']).toordinal()
        key = (account.get('currency', 'USD'), is_on_budget(account), abs(row['amount']))
        eligible.append((row, ordinal, key))
        if row['amount'] < 0:
            inflows[key].append((ordinal, row['id'], row))
    for values in inflows.values():
        values.sort(key=lambda value: (value[0], value[1]))
    proposed, alternatives = [], defaultdict(int)
    for left, ordinal, key in eligible:
        if left['amount'] < 0:
            continue
        values = inflows[key]
        start = bisect_left(values, ordinal - MAX_DAYS, key=lambda value: value[0])
        end = bisect_right(values, ordinal + MAX_DAYS, key=lambda value: value[0])
        for other_date, _, right in values[start:end]:
            if left['accountId'] == right['accountId'] or not (in_scope(left) or in_scope(right)):
                continue
            if len(proposed) == MAX_CANDIDATES:
                raise StoreError('More than 1,000 possible pairs. Narrow dates/accounts; no results were silently truncated.')
            legs = [{**public_transaction({**row, 'currency': accounts[row['accountId']].get('currency', 'USD')}), 'accountName': accounts[row['accountId']]['name']} for row in (left, right)]
            hinted = any(row.get('isTransfer') or row.get('inferredTransfer') for row in (left, right))
            proposed.append({'transactionIds': [left['id'], right['id']], 'transactions': legs,
                             'daysApart': abs(other_date - ordinal), 'bankHint': hinted,
                             'reason': 'Equal opposite amounts, same currency and budget scope, different accounts, within seven days.'})
            for row in (left, right):
                alternatives[row['id']] += 1
    proposed.sort(key=lambda row: (not row['bankHint'], row['daysApart'], row['transactionIds']))
    for row in proposed:
        row['ambiguous'] = any(alternatives[identifier] > 1 for identifier in row['transactionIds'])
    end = offset + limit
    return {'candidates': proposed[offset:end], 'total': len(proposed),
            'next': end if end < len(proposed) else None, 'requiresConfirmation': True,
            'reviewedAutomatically': False, 'maxDaysApart': MAX_DAYS}
