"""Deterministic transfer proposals and explicitly confirmed, reversible pairs."""

from bisect import bisect_left, bisect_right
from collections import ChainMap, defaultdict
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


def incoming_links(transactions):
    incoming = defaultdict(set)
    for row in transactions.values():
        if type(row.get('transferPeerId')) is str:
            incoming[row['transferPeerId']].add(row['id'])
    return incoming


def valid_pair(row, transactions, accounts, categories=None, *, incoming=None):
    """Prove one reciprocal pair, including absence of an incoming third leg."""
    peer_id = row.get('transferPeerId')
    peer = transactions.get(peer_id) if type(peer_id) is str else None
    if (peer is None or peer.get('transferPeerId') != row.get('id')
            or row.get('accountId') not in accounts or peer.get('accountId') not in accounts
            or not _compatible(row, peer, accounts, categories)
            or abs((date.fromisoformat(row['date']) - date.fromisoformat(peer['date'])).days) > MAX_DAYS
            or not row.get('isTransfer') or not peer.get('isTransfer')):
        return False
    incoming = incoming_links(transactions) if incoming is None else incoming
    return incoming.get(row['id'], set()) == {peer['id']} and incoming.get(peer['id'], set()) == {row['id']}


def archived_pair(row, transactions, accounts, categories=None, *, incoming=None):
    """Prove a retained pair whose two explicitly voided legs are excluded."""
    peer = transactions.get(row.get('transferPeerId'))
    if peer is None or any(record.get('voided') is not True or record.get('deleted') is not True
                           or record.get('reviewed') for record in (row, peer)):
        return False
    restored = ChainMap({record['id']: {**record, 'deleted': False} for record in (row, peer)}, transactions)
    return valid_pair(restored[row['id']], restored, accounts, categories, incoming=incoming)


def _identifier(value):
    if type(value) is not str or not 1 <= len(value) <= 200 or '\x00' in value:
        raise StoreError('Transaction IDs must be nonempty text of at most 200 characters.')
    try:
        value.encode('utf-8')
    except UnicodeError:
        raise StoreError('Transaction IDs must contain valid UTF-8 text.') from None


def validate_pairs(pairs, reviewed=False):
    """One bounded, explicit, disjoint intent; never split or infer counterparts."""
    if type(pairs) is not list or not 1 <= len(pairs) <= 100:
        raise StoreError('A transfer batch must contain 1–100 explicit pairs; it is never split.')
    if type(reviewed) is not bool:
        raise StoreError('reviewed must be true or false.')
    seen = set()
    for index, pair in enumerate(pairs, 1):
        if type(pair) is not dict or pair.keys() != {'outId', 'inId'}:
            raise StoreError(f'Pair {index}: supply exactly outId and inId.')
        for identifier in pair.values():
            _identifier(identifier)
            if identifier in seen:
                raise StoreError(f'Pair {index}: duplicate transaction ID {identifier!r}; every pair must be disjoint.')
            seen.add(identifier)
    return deepcopy(pairs)


def _public(state, row):
    account = state['accounts'][row['accountId']]
    return {**public_transaction({**row, 'currency': account.get('currency', 'USD')}),
            'accountName': account['name'],
            **{key: deepcopy(row[key]) for key in ('original', 'categoryEdited', 'deleted', 'voided', 'providerDeleted') if key in row}}


def _fingerprint(state, action, ids, changes, extras=None):
    # Include exact raw rows, current account scopes and category treatment.
    observed = [action, extras, changes,
                [state['transactions'][identifier] for identifier in ids],
                [state['accounts'][state['transactions'][identifier]['accountId']] for identifier in ids],
                [state['categories'].get(state['transactions'][identifier].get('categoryId')) for identifier in ids]]
    return sha256(dumps(observed, sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def plan_many(state, pairs, *, reviewed=False, fingerprint=None, apply=False):
    """Validate every explicit pair before publishing any financial annotation."""
    pairs = validate_pairs(pairs, reviewed)
    changes, updates, contexts = [], {}, []
    ids = [identifier for pair in pairs for identifier in (pair['outId'], pair['inId'])]
    for index, pair in enumerate(pairs, 1):
        left, right = (state['transactions'].get(pair[key]) for key in ('outId', 'inId'))
        if left is None or right is None:
            raise StoreError(f'Pair {index}: a transaction is missing from this workspace.')
        if (left['amount'] != 0 or right['amount'] != 0) and (left['amount'] <= 0 or right['amount'] >= 0):
            raise StoreError(f'Pair {index}: outId must be an outflow and inId an inflow, or both IDs must identify existing posted zero-value entries. Zero pairs have no inferred direction.')
        candidate = {**state, 'transactions': dict(state['transactions'])}
        try:
            plan(candidate, [pair['outId'], pair['inId']], apply=True)
        except StoreError as error:
            raise StoreError(f'Pair {index}: {error}') from None
        accounts = [state['accounts'][row['accountId']] for row in (left, right)]
        contexts.append({**pair, 'crossesBudgetScope': is_on_budget(accounts[0]) != is_on_budget(accounts[1]),
                         'archivedAccounts': [account['id'] for account in accounts if account.get('closed', False)]})
        for row in (left, right):
            after = candidate['transactions'][row['id']]
            after['reviewed'] = reviewed
            updates[row['id']] = after
            changes.append({'id': row['id'], 'before': _public(state, row), 'after': _public(state, after)})
    observed = _fingerprint(state, 'match', ids, changes, reviewed)
    if fingerprint is not None and fingerprint != observed:
        raise StoreError('Transfer batch changed. Preview the exact batch again; nothing was changed.')
    if apply:
        state['transactions'].update(updates)
    return {'action': 'match', 'pairCount': len(pairs), 'count': len(changes), 'changed': len(changes),
            'reviewed': reviewed, 'fingerprint': observed, 'pairs': contexts, 'changes': changes}


def _audit_issue(state, row, incoming=None):
    category = state['categories'].get(row.get('categoryId'), {})
    from .review import effective_transfer
    if category and category.get('kind', 'expense') != 'transfer' and not effective_transfer(row, state['categories']) and 'transferPeerId' not in row:
        return None
    if not ('transferPeerId' in row or row.get('isTransfer') or row.get('inferredTransfer') or category.get('kind') == 'transfer'):
        return None
    if (valid_pair(row, state['transactions'], state['accounts'], state['categories'], incoming=incoming)
            or archived_pair(row, state['transactions'], state['accounts'], state['categories'], incoming=incoming)):
        return None
    if row['amount'] == 0:
        code, message = 'zero_amount', 'A zero-value hint needs an explicitly confirmed pair of existing posted zero entries in different same-currency accounts; no direction or counterpart is inferred.'
    elif 'transferPeerId' in row:
        code, message = 'invalid_pair', 'The stored link is not an exclusive reciprocal pair of equal opposite posted amounts.'
    else:
        code, message = 'missing_pair', 'A transfer hint or category has no confirmed reciprocal counterpart.'
    return {'code': code, 'message': message}


def audit(state, parameters=None):
    """Complete paginated diagnostics, without modifying legacy saved records."""
    parameters = {} if parameters is None else parameters
    if type(parameters) is not dict or parameters.keys() - {'since', 'until', 'offset', 'limit'}:
        raise StoreError('Transfer audit accepts since, until, offset and limit only.')
    for key in ('since', 'until'):
        if parameters.get(key) is not None:
            validate_date(parameters[key])
    if parameters.get('since') and parameters.get('until') and parameters['since'] > parameters['until']:
        raise StoreError('Transfer since must not be after until.')
    offset, limit = parameters.get('offset', 0), parameters.get('limit', 100)
    if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
        raise StoreError('Use a nonnegative offset and a limit of 1–100 audit rows.')
    issues, incoming = [], incoming_links(state['transactions'])
    for row in sorted(state['transactions'].values(), key=lambda value: value['id']):
        if (parameters.get('since') and row['date'] < parameters['since'] or parameters.get('until') and row['date'] > parameters['until']):
            continue
        issue = _audit_issue(state, row, incoming)
        if issue is not None:
            issues.append({'id': row['id'], **issue, 'transaction': _public(state, row),
                           'repairable': bool(row.get('reviewed') or 'transferPeerId' in row or state['categories'].get(row.get('categoryId'), {}).get('kind') == 'transfer')})
    end = offset + limit
    return {'issues': issues[offset:end], 'total': len(issues), 'next': end if end < len(issues) else None,
            'zeroValuePolicy': 'Existing posted zero entries may be explicitly paired across different same-currency accounts and then reviewed. Unpaired zero hints remain unreviewed; no entries or direction are invented.'}


def validate_repair_ids(transaction_ids):
    if type(transaction_ids) is not list or not 1 <= len(transaction_ids) <= 200:
        raise StoreError('A transfer repair must select 1–200 explicit transaction IDs.')
    for identifier in transaction_ids:
        _identifier(identifier)
    if len(set(transaction_ids)) != len(transaction_ids):
        raise StoreError('Transfer repair transaction IDs must be distinct.')
    return list(transaction_ids)


def repair_plan(state, transaction_ids, *, fingerprint=None, apply=False):
    """Clear explicitly selected invalid classifications/reviews, never invent legs."""
    transaction_ids = validate_repair_ids(transaction_ids)
    selected, updates, changes = set(transaction_ids), {}, []
    for identifier in transaction_ids:
        row = state['transactions'].get(identifier)
        if row is None:
            raise StoreError(f'{identifier}: transaction not found in this workspace.')
        if _audit_issue(state, row) is None:
            raise StoreError(f'{identifier}: transaction has no invalid transfer state to repair.')
        linked = {other['id'] for other in state['transactions'].values()
                  if other.get('transferPeerId') == identifier or other['id'] == row.get('transferPeerId')}
        if linked - selected:
            raise StoreError(f'{identifier}: select every linked counterpart for atomic repair: {", ".join(sorted(linked - selected))}.')
        after = deepcopy(row)
        after.pop('transferPeerId', None)
        after['reviewed'] = False
        if state['categories'].get(after.get('categoryId'), {}).get('kind') == 'transfer':
            after['categoryId'] = None
            after['categoryEdited'] = True
            after.pop('categoryAgent', None)
        # Preserve imported evidence, but never turn a hint into a confirmed pair.
        after['isTransfer'] = after.get('categoryId') is None and bool(after.get('inferredTransfer', False))
        updates[identifier] = after
        changes.append({'id': identifier, 'before': _public(state, row), 'after': _public(state, after)})
    observed = _fingerprint(state, 'repair', transaction_ids, changes)
    if fingerprint is not None and fingerprint != observed:
        raise StoreError('Transfer repair changed. Preview the exact selection again; nothing was changed.')
    if apply:
        state['transactions'].update(updates)
    return {'action': 'repair', 'count': len(changes), 'changed': sum(change['before'] != change['after'] for change in changes),
            'fingerprint': observed, 'changes': changes}


def _compatible(left, right, accounts, categories=None):
    a, b = accounts[left['accountId']], accounts[right['accountId']]
    return (left['id'] != right['id'] and a['id'] != b['id']
            and a.get('currency', 'USD') == b.get('currency', 'USD')
            and left['amount'] == -right['amount']
            and not any(row.get('deleted') or row.get('pending') or row.get('categoryId') is not None and (categories or {}).get(row['categoryId'], {}).get('kind') != 'transfer' for row in (left, right)))


def validate_matches(state):
    """Pair identities cannot dangle or conceal a cross-currency/scope imbalance."""
    incoming = incoming_links(state['transactions'])
    for row in state['transactions'].values():
        if 'transferPeerId' not in row:
            continue
        if not (valid_pair(row, state['transactions'], state['accounts'], state.get('categories'), incoming=incoming)
                or archived_pair(row, state['transactions'], state['accounts'], state.get('categories'), incoming=incoming)):
            raise StoreError('Invalid transfer pair. Unmatch it before changing its amount, account, category or budget scope.')


def reconcile_matches(state):
    """Provider corrections/removals invalidate both links and require fresh review."""
    invalid = set()
    incoming = incoming_links(state['transactions'])
    for row in state['transactions'].values():
        peer = state['transactions'].get(row.get('transferPeerId'))
        if ('transferPeerId' in row
                and not valid_pair(row, state['transactions'], state['accounts'], state.get('categories'), incoming=incoming)
                and not archived_pair(row, state['transactions'], state['accounts'], state.get('categories'), incoming=incoming)):
            invalid.add(row['id'])
            if peer is not None:
                invalid.add(peer['id'])
    for identifier in invalid:
        row = state['transactions'][identifier]
        row.pop('transferPeerId', None)
        if state.get('categories', {}).get(row.get('categoryId'), {}).get('kind') == 'transfer':
            row['categoryId'] = None
            row['categoryEdited'] = True
            row.pop('categoryAgent', None)
        row['isTransfer'] = row.get('inferredTransfer', False)
        row['reviewed'] = False


def plan(state, transaction_ids, *, action='match', fingerprint=None, apply=False):
    if (type(transaction_ids) is not list or len(transaction_ids) != 2
            or any(type(value) is not str or not 1 <= len(value) <= 200 for value in transaction_ids)
            or len(set(transaction_ids)) != 2 or type(action) is not str or action not in {'match', 'unmatch'}):
        raise StoreError('Choose exactly two distinct transaction IDs and match or unmatch.')
    for identifier in transaction_ids:
        _identifier(identifier)
    rows = [state['transactions'].get(identifier) for identifier in transaction_ids]
    if any(row is None or row.get('deleted') for row in rows):
        raise StoreError('A transfer transaction is missing or deleted in this workspace.')
    left, right = rows
    accounts = state['accounts']
    if any(row['accountId'] not in accounts for row in rows):
        raise StoreError('A transfer account is missing from this workspace.')
    if any(row.get('transferPeerId') in transaction_ids and row['id'] not in transaction_ids for row in state['transactions'].values()):
        raise StoreError('Another transaction already references this pair. Audit and repair its links first.')
    if action == 'match':
        if any(row.get('transferPeerId') for row in rows):
            raise StoreError('A transaction is already matched. Inspect or unmatch its existing pair first.')
        if not _compatible(left, right, accounts, state.get('categories')):
            raise StoreError('A transfer needs existing posted transactions that are unclassified or explicitly Transfer, with opposite equal amounts (including two explicit zeros) in different accounts of the same currency.')
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
            if state.get('categories', {}).get(after.get('categoryId'), {}).get('kind') == 'transfer':
                after['categoryId'] = None
                after['categoryEdited'] = True
                after.pop('categoryAgent', None)
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
        if (not account or row.get('deleted') or row.get('pending')
                or row.get('categoryId') is not None and (categories or {}).get(row['categoryId'], {}).get('kind') != 'transfer' or row.get('transferPeerId') or not row['amount']
                or currency is not None and account.get('currency', 'USD') != currency):
            continue
        ordinal = date.fromisoformat(row['date']).toordinal()
        key = (account.get('currency', 'USD'), abs(row['amount']))
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
                             'reason': 'Equal opposite amounts, same currency, different accounts, within seven days.'})
            for row in (left, right):
                alternatives[row['id']] += 1
    proposed.sort(key=lambda row: (not row['bankHint'], row['daysApart'], row['transactionIds']))
    for row in proposed:
        row['ambiguous'] = any(alternatives[identifier] > 1 for identifier in row['transactionIds'])
    end = offset + limit
    return {'candidates': proposed[offset:end], 'total': len(proposed),
            'next': end if end < len(proposed) else None, 'requiresConfirmation': True,
            'reviewedAutomatically': False, 'maxDaysApart': MAX_DAYS}
