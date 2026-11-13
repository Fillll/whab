"""Append-only financial history, deterministic snapshots and guarded reversals.

No I/O or provider replay. Persistence adapters commit these records with the
financial mutation. A change record never carries banking credentials/cursors.
"""

import json
from copy import deepcopy
from datetime import datetime, timezone
from hashlib import sha256
from operator import itemgetter
from uuid import uuid4

from .errors import StoreError
from .portable import (
    ACCOUNT_FIELDS,
    ASSIGNMENT_FIELDS,
    CATEGORY_FIELDS,
    FUTURE_FUNDING_FIELDS,
    ROLLOVER_MODE_FIELDS,
    TRANSACTION_FIELDS,
    validate_portable_budget,
)

_COLLECTIONS = {
    'accounts': ('id',), 'categories': ('id',), 'transactions': ('id',),
    'assignments': ('month', 'categoryId'), 'futureFunding': ('month', 'currency'),
    'rolloverModes': ('month', 'currency'),
    'categoryGroups': ('id',),
}
_FIELDS = {
    'accounts': (ACCOUNT_FIELDS, {'bankWebsite', 'nameOverridden', 'closed'}),
    'categories': (CATEGORY_FIELDS, set()),
    'transactions': (TRANSACTION_FIELDS, {'categoryAgent', 'original', 'transferPeerId'}),
    'assignments': (ASSIGNMENT_FIELDS, set()),
    'futureFunding': (FUTURE_FUNDING_FIELDS, set()),
    'rolloverModes': (ROLLOVER_MODE_FIELDS, set()),
    'categoryGroups': ({'id', 'name'}, set()),
}
REVERSIBLE = frozenset({'match_transfer', 'apply_transfer_match',
    'add_account', 'update_account', 'add_category', 'add_category_group', 'move_category', 'delete_category', 'delete_category_group', 'add_transaction',
    'edit_transactions', 'classify_accounts', 'edit_transaction', 'edit_transaction_details', 'review_transactions', 'rename_category', 'append_transaction_note',
    'assign', 'assign_next_month', 'set_rollover_mode',
})
_ENTRY_FIELDS = {'id', 'time', 'actor', 'actorKind', 'batchId', 'operation', 'requestId',
                 'changes', 'beforeDigest', 'afterDigest', 'reversible', 'action',
                 'target', 'previousHash', 'hash'}


def digest(value):
    return sha256(json.dumps(value, sort_keys=True, ensure_ascii=True,
                             separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def label(value, *, nullable=False, limit=100):
    if value is None and nullable:
        return value
    if type(value) is not str or not 1 <= len(value) <= limit or value.strip() != value or not value.isprintable():
        raise StoreError('Invalid history label.')
    return value


def context(value=None, *, operation='change'):
    value = {} if value is None else value
    if type(value) is not dict or value.keys() - {'actor', 'actorKind', 'batchId', 'requestId', 'operation', 'action', 'target'}:
        raise StoreError('Invalid history context.')
    result = {'actor': value.get('actor', 'User'), 'actorKind': value.get('actorKind', 'user'),
              'batchId': value.get('batchId'), 'requestId': value.get('requestId'),
              'operation': value.get('operation', operation), 'action': value.get('action', 'change'),
              'target': value.get('target')}
    for key in ('actor', 'operation'):
        label(result[key], limit=100)
    for key in ('batchId', 'requestId', 'target'):
        label(result[key], nullable=True, limit=200)
    if result['actorKind'] not in {'user', 'agent', 'provider', 'system'} or result['action'] not in {'change', 'undo', 'redo', 'restore', 'snapshot'}:
        raise StoreError('Invalid history context.')
    return result


def financial(snapshot, *, trusted=False, transactions_sorted=False):
    if type(snapshot) is not dict:
        raise StoreError('Invalid financial snapshot.')
    result = {k: v if trusted else deepcopy(v) for k, v in snapshot.items() if k != 'history'}
    if not trusted:
        result = validate_portable_budget(result)
    for section, fields in _COLLECTIONS.items():
        if section in result and not (trusted and transactions_sorted and section == 'transactions'):
            result[section].sort(key=itemgetter(*fields))
    return result


def flatten(snapshot):
    result = {('budgetStartMonth',): snapshot['budgetStartMonth']}
    for section, keys in _COLLECTIONS.items():
        for row in snapshot.get(section, []):
            result[(section, *(row[k] for k in keys))] = row
    return result


def unflatten(rows):
    result = {'formatVersion': 1, 'budgetStartMonth': rows[('budgetStartMonth',)], **{key: [] for key in _COLLECTIONS}}
    for path, value in sorted(rows.items()):
        if path[0] != 'budgetStartMonth':
            result[path[0]].append(deepcopy(value))
    return financial(result)


def difference(before, after):
    a, b = flatten(before), flatten(after)
    return [{'path': list(key), 'before': deepcopy(a.get(key)), 'after': deepcopy(b.get(key))}
            for key in sorted(a.keys() | b.keys()) if a.get(key) != b.get(key)]


def _change(change):
    if type(change) is not dict or change.keys() != {'path', 'before', 'after'}:
        raise StoreError('Invalid history change.')
    path = change['path']
    if type(path) is not list or not path or any(type(key) is not str or not key or len(key) > 200 for key in path):
        raise StoreError('Invalid history record path.')
    if path[0] == 'budgetStartMonth':
        if len(path) != 1 or any(type(change[k]) is not str for k in ('before', 'after')):
            raise StoreError('Invalid history month.')
        return
    if path[0] not in _COLLECTIONS or len(path) != len(_COLLECTIONS[path[0]]) + 1:
        raise StoreError('Invalid history record path.')
    required, optional = _FIELDS[path[0]]
    for side in ('before', 'after'):
        row = change[side]
        if row is not None and (type(row) is not dict or not required <= row.keys()
                                or row.keys() - required - optional
                                or [row[k] for k in _COLLECTIONS[path[0]]] != path[1:]):
            raise StoreError('Invalid history record fields.')
        if row is not None and 'original' in row and (type(row['original']) is not dict or row['original'].keys() - {'accountId', 'date', 'merchant', 'amount'}):
            raise StoreError('Invalid original history fields.')


def validate_history(value):
    if type(value) is not dict or value.keys() != {'formatVersion', 'baseline', 'entries'} or type(value['formatVersion']) is not int or value['formatVersion'] != 1:
        raise StoreError('Invalid budget history.')
    if type(value['entries']) is not list:
        raise StoreError('Invalid budget history entries.')
    if type(value['baseline']) is not dict or 'history' in value['baseline']:
        raise StoreError('Nested budget history is not allowed.')
    baseline = financial(value['baseline'])
    previous = digest(baseline)
    previous_state_digest = previous
    rows = flatten(baseline)
    seen = set()
    for entry in value['entries']:
        if type(entry) is not dict or not _ENTRY_FIELDS <= entry.keys() or entry.keys() - _ENTRY_FIELDS - {'snapshot'}:
            raise StoreError('Invalid history entry fields.')
        label(entry['id'], limit=200)
        if entry['id'] in seen or entry['previousHash'] != previous:
            raise StoreError('Budget history chain is inconsistent.')
        seen.add(entry['id'])
        context({k: entry[k] for k in ('actor', 'actorKind', 'batchId', 'operation', 'requestId', 'action', 'target')})
        try:
            stamp = datetime.fromisoformat(entry['time'])
        except (ValueError, TypeError):
            raise StoreError('Invalid history time.') from None
        if stamp.tzinfo is None or type(entry['reversible']) is not bool or type(entry['changes']) is not list:
            raise StoreError('Invalid history entry.')
        for change in entry['changes']:
            _change(change)
        if entry['beforeDigest'] != previous_state_digest:
            raise StoreError('Budget history state chain is inconsistent.')
        _apply(rows, entry['changes'], inverse=False)
        previous_state_digest = entry['afterDigest']
        if any(type(entry[k]) is not str or len(entry[k]) != 64 or any(c not in '0123456789abcdef' for c in entry[k]) for k in ('beforeDigest', 'afterDigest', 'hash')):
            raise StoreError('Invalid history digest.')
        if 'snapshot' in entry:
            if type(entry['snapshot']) is not dict or 'history' in entry['snapshot'] or digest(financial(entry['snapshot'])) != entry['afterDigest']:
                raise StoreError('Invalid history snapshot.')
        if digest({k: v for k, v in entry.items() if k != 'hash'}) != entry['hash']:
            raise StoreError('Budget history entry checksum does not match.')
        previous = entry['hash']
    if digest(unflatten(rows)) != previous_state_digest:
        raise StoreError('Budget history final state digest does not match.')
    return {'formatVersion': 1, 'baseline': baseline, 'entries': deepcopy(value['entries'])}


def append(journal, before, after, metadata=None, *, trusted=False, before_digest=None, after_digest=None, changes=None):
    if not trusted:
        before, after = financial(before), financial(after)
    changes = difference(before, after) if changes is None else changes
    info = context(metadata)
    if not changes and info['action'] != 'snapshot' and info['operation'] not in {'apply_sync', 'save_receipt', 'delete_receipt', 'import_workspace'}:
        return journal
    journal = {**journal, 'entries': list(journal['entries'])} if journal else {'formatVersion': 1, 'baseline': deepcopy(before), 'entries': []}
    before_digest = before_digest or digest(before)
    entry = {'id': str(uuid4()), 'time': datetime.now(timezone.utc).isoformat(), **info,
             'changes': changes, 'beforeDigest': before_digest, 'afterDigest': after_digest or digest(after),
             'reversible': info['operation'] in REVERSIBLE,
             'previousHash': journal['entries'][-1]['hash'] if journal['entries'] else before_digest}
    if info['action'] == 'snapshot' or (len(journal['entries']) + 1) % 1000 == 0:
        entry['snapshot'] = deepcopy(after)
    entry['hash'] = digest(entry)
    journal['entries'].append(entry)
    return journal


def listing(journal, *, limit=50, before=None):
    if type(limit) is not int or not 1 <= limit <= 100:
        raise StoreError('History limit must be between 1 and 100.')
    entries = journal['entries'] if journal else []
    if before is not None:
        ids = [e['id'] for e in entries]
        if before not in ids:
            raise StoreError('History cursor not found.')
        entries = entries[:ids.index(before)]
    chosen = list(reversed(entries[-limit:]))
    return {'total': len(journal['entries']) if journal else 0,
            'entries': [{k: deepcopy(v) for k, v in e.items() if k not in {'changes', 'snapshot'}} | {'changeCount': len(e['changes']), 'hasSnapshot': 'snapshot' in e} for e in chosen],
            'next': chosen[-1]['id'] if len(entries) > limit else None}


def snapshot_at(journal, target):
    if target == 'baseline':
        return deepcopy(journal['baseline'])
    entries = journal['entries']
    ids = [e['id'] for e in entries]
    if target not in ids:
        raise StoreError('History entry not found.')
    endpoint = ids.index(target)
    start = max((i for i in range(endpoint + 1) if 'snapshot' in entries[i]), default=-1)
    rows = flatten(entries[start]['snapshot'] if start >= 0 else journal['baseline'])
    for entry in entries[start + 1:endpoint + 1]:
        _apply(rows, entry['changes'], inverse=False)
    return unflatten(rows)


def _apply(rows, changes, *, inverse):
    for change in reversed(changes) if inverse else changes:
        path = tuple(change['path'])
        expected, wanted = (change['after'], change['before']) if inverse else (change['before'], change['after'])
        if rows.get(path) != expected:
            raise StoreError('Later changes conflict with this history operation. Refresh and review them first.')
        if wanted is None:
            rows.pop(path, None)
        else:
            rows[path] = deepcopy(wanted)


def _annotations_only(entries):
    allowed = {'categoryId', 'categoryAgent', 'categoryEdited', 'isTransfer', 'transferPeerId',
               'inferredTransfer', 'reviewed', 'note', 'merchant', 'merchantLogo', 'date'}
    return all(change['path'][0] == 'transactions'
               and change['before'] is not None and change['after'] is not None
               and {key for key in change['before'].keys() | change['after'].keys()
                    if change['before'].get(key) != change['after'].get(key)} <= allowed
               for entry in entries for change in entry['changes'])


def preview(journal, current, action, target):
    current = financial(current)
    if journal:
        journal = validate_history(journal)
    if action == 'snapshot':
        return {'action': action, 'target': None, 'changes': [], 'fingerprint': digest([digest(current), 'snapshot']), 'state': current}
    if not journal:
        raise StoreError('This budget has no recorded history yet.')
    entries = journal['entries']
    if action == 'restore':
        expected = entries[-1]['afterDigest'] if entries else digest(journal['baseline'])
        if digest(current) != expected:
            raise StoreError('Unrecorded import or external changes prevent restoration. Export the historical snapshot for inspection instead.')
        ids = [e['id'] for e in entries]
        if target != 'baseline' and target not in ids:
            raise StoreError('History snapshot not found.')
        index = -1 if target == 'baseline' else ids.index(target)
        if any(not e['reversible'] and e['action'] == 'change' for e in entries[index + 1:]):
            raise StoreError('Restoration crosses provider/import or attachment changes. Export the historical snapshot for inspection instead.')
        wanted = snapshot_at(journal, target)
    elif action in {'undo', 'redo'}:
        selected = [e for e in entries if e['action'] == 'change' and (e['id'] == target or e['batchId'] == target)]
        if not selected or any(not e['reversible'] for e in selected):
            raise StoreError('History target is missing or contains changes that cannot be reversed.')
        related = [e for e in entries if e['target'] == target and e['action'] in {'undo', 'redo'}]
        if action == 'undo' and related and related[-1]['action'] == 'undo':
            raise StoreError('This history target is already undone.')
        if action == 'redo' and (not related or related[-1]['action'] != 'undo'):
            raise StoreError('Redo requires a matching undo.')
        if not _annotations_only(selected):
            anchor = entries.index(related[-1]) if related else min(entries.index(entry) for entry in selected)
            selected_ids = {entry['id'] for entry in selected}
            if any(entry['changes'] and entry['id'] not in selected_ids for entry in entries[anchor + 1:]):
                raise StoreError('Later financial changes may depend on this earlier state. Restore a complete reviewed snapshot instead of reversing this entry or batch.')
        rows = flatten(current)
        if action == 'undo':
            for entry in reversed(selected):
                _apply(rows, entry['changes'], inverse=True)
        else:
            _apply(rows, related[-1]['changes'], inverse=True)
        wanted = unflatten(rows)
    else:
        raise StoreError('Choose undo, redo, restore or snapshot.')
    changes = difference(current, wanted)
    return {'action': action, 'target': target, 'changes': changes,
            'fingerprint': digest([digest(current), action, target, changes]), 'state': wanted}


def apply_to_state(state, wanted):
    """Replace only allowlisted financial fields, retaining provider associations."""
    from .ledger_state import restore_snapshot
    old_accounts = state['accounts']
    candidate = dict(state)
    restore_snapshot(candidate, wanted, mark_external_stale=False)
    removed_accounts = old_accounts.keys() - candidate['accounts'].keys()
    linked = {key for key, row in old_accounts.items() if row.get('itemId') is not None}
    linked.update(link['accountId'] for item in state.get('items', {}).values()
                  for link in (item.get('syncSettings') or {}).values())
    if removed_accounts & linked:
        raise StoreError('This reversal would detach a linked bank account. Review the bank setup first.')
    if any(old_accounts[key]['currency'] != candidate['accounts'][key]['currency'] for key in linked & candidate['accounts'].keys()):
        raise StoreError('This reversal would change a linked bank account currency.')
    allowed = ACCOUNT_FIELDS | {'bankWebsite', 'nameOverridden', 'closed'}
    for identifier, account in candidate['accounts'].items():
        previous = old_accounts.get(identifier, {})
        candidate['accounts'][identifier] = {**{k: v for k, v in previous.items() if k not in allowed}, **account}
    if any(identifier not in candidate['transactions'] for identifier in state.get('receipts', {})):
        raise StoreError('This reversal would orphan a receipt attachment.')
    from .bank_setup import reconcile_account_closed
    state['items'] = deepcopy(state.get('items', {}))
    for identifier, account in candidate['accounts'].items():
        reconcile_account_closed(state, identifier, old_accounts.get(identifier, {}).get('closed', False), account.get('closed', False))
    for name in ('budgetStartMonth', 'accounts', 'categories', 'transactions', 'budget', 'futureFunding', 'rolloverModes'):
        state[name] = candidate[name]
    state.pop('categoryGroups', None)
    if candidate.get('categoryGroups'):
        state['categoryGroups'] = candidate['categoryGroups']


def record_financial(journal, before, after, metadata=None, *, digest_snapshot=digest, changes=None, transactions_sorted=False):
    """Commit adapters supply detached allowlisted snapshots from one native transaction."""
    info = context(metadata)
    old, new = (financial(value, trusted=True, transactions_sorted=transactions_sorted) for value in (before, after))
    unchanged = not changes if changes is not None else old == new
    if unchanged and info['action'] != 'snapshot' and info['operation'] not in {'apply_sync', 'save_receipt', 'delete_receipt', 'import_workspace'}:
        return journal
    # Pristine restores preserve their incoming journal. Reconcile any deliberate
    # provider detachment or external edit when the first new change is made.
    old_digest = None
    if journal:
        last = journal['entries'][-1] if journal['entries'] else None
        expected = last['afterDigest'] if last else digest(journal['baseline'])
        old_digest = digest_snapshot(old)
        if old_digest != expected:
            previous = snapshot_at(journal, last['id'] if last else 'baseline')
            journal = append(journal, previous, old, {'actor': 'System', 'actorKind': 'system', 'operation': 'import_workspace'})
    return append(journal, old, new, info, trusted=True, before_digest=old_digest, after_digest=digest_snapshot(new), changes=changes)


def record_states(before, after, metadata=None):
    """Record one adapter-owned commit without resetting imported journals."""
    from .ledger_state import portable_snapshot
    journal = after.get('history') or before.get('history')
    updated = record_financial(journal, portable_snapshot(before, validate=False),
                               portable_snapshot(after, validate=False), metadata)
    if updated is not None:
        after['history'] = updated


def public_preview(state, action, target):
    from .ledger_state import portable_snapshot
    result = preview(state.get('history'), portable_snapshot(state, validate=False), action, target)
    if action != 'snapshot':
        apply_to_state(dict(state), result['state'])
    return {k: v for k, v in result.items() if k != 'state'}


def apply_request(state, action, target, fingerprint, metadata=None):
    from .ledger_state import portable_snapshot
    result = preview(state.get('history'), portable_snapshot(state, validate=False), action, target)
    if fingerprint != result['fingerprint']:
        raise StoreError('Budget changed since this preview. Preview the history operation again.')
    apply_to_state(state, result['state'])
    return {'action': action, 'target': target, 'changeCount': len(result['changes'])}
