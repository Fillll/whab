"""Core transfer plans persisted in one native transaction with bounded writes."""

from whab.domain.transfer_matching import audit, plan_many, repair_plan, validate_pairs


def retains_pairs(state):
    """Fence older native readers, including pairs retained only by audit history."""
    journal = state.get('history') or {}
    transactions = state['transactions']
    rows = transactions.values() if isinstance(transactions, dict) else transactions
    return (any(row.get('transferPeerId') for row in rows)
            or any(row.get('transferPeerId') for row in journal.get('baseline', {}).get('transactions', ()))
            or any(row is not None and row.get('transferPeerId') for entry in journal.get('entries', ())
                   for change in entry['changes'] if change['path'][0] == 'transactions'
                   for row in (change['before'], change['after'])))


def related_ids(store, workspace, transaction_ids):
    """Read both directions so an unseen third leg cannot share a counterpart."""
    selected = set(transaction_ids)
    pending = set(selected)
    while pending:
        placeholders = ','.join('?' for _ in pending)
        rows = store.db.execute(
            'SELECT id,transfer_peer_id FROM transactions WHERE workspace=? AND id IN (' + placeholders + ') '
            'UNION ALL SELECT id,transfer_peer_id FROM transactions WHERE workspace=? AND transfer_peer_id IN (' + placeholders + ')',
            (workspace, *pending, workspace, *pending),
        )
        found = {identifier for row in rows for identifier in row if identifier is not None}
        pending = found - selected
        selected.update(found)
    return tuple(sorted(selected))


def context(store, workspace, transaction_ids):
    store._require_workspace(workspace)
    return store._read_state(workspace, transaction_ids=related_ids(store, workspace, transaction_ids), include_history=False)


def public_validity(store, workspace, rows):
    """Project peer validity using the complete graph, outside register filters."""
    selected = [row['id'] for row in rows if row.get('transferPeerId')]
    if selected:
        from whab.domain.transfer_matching import incoming_links, valid_pair
        state = context(store, workspace, selected)
        incoming = incoming_links(state['transactions'])
        for row in rows:
            if row.get('transferPeerId'):
                row['transferPairValid'] = valid_pair(state['transactions'][row['id']], state['transactions'],
                    state['accounts'], state['categories'], incoming=incoming)
    return rows


def preview(store, workspace, pairs, reviewed=False):
    pairs = validate_pairs(pairs, reviewed=reviewed)
    with store._snapshot():
        state = context(store, workspace, [key for pair in pairs for key in (pair['outId'], pair['inId'])])
        return plan_many(state, pairs, reviewed=reviewed)


def apply(store, workspace, pairs, reviewed=False, fingerprint=None):
    pairs = validate_pairs(pairs, reviewed=reviewed)
    identifiers = [key for pair in pairs for key in (pair['outId'], pair['inId'])]
    with store._authority_write(workspace=workspace, transaction_ids=identifiers) as write:
        state = context(store, workspace, identifiers)
        result = plan_many(state, pairs, reviewed=reviewed, fingerprint=fingerprint, apply=True)
        _save(store, workspace, state, result, write, 'match_transfers')
        return result


def read_audit(store, workspace, parameters):
    with store._snapshot():
        return audit(store._read_state(workspace, include_history=False), parameters)


def preview_repair(store, workspace, transaction_ids):
    with store._snapshot():
        # Core owns selection validation, including duplicate/missing identities.
        state = store._read_state(workspace, include_history=False)
        return repair_plan(state, transaction_ids)


def repair(store, workspace, transaction_ids, fingerprint):
    with store._authority_write(workspace=workspace) as write:
        state = store._read_state(workspace, include_history=False)
        result = repair_plan(state, transaction_ids, fingerprint=fingerprint, apply=True)
        _save(store, workspace, state, result, write, 'repair_transfers')
        return result


def _save(store, workspace, state, result, write, operation):
    # Every target and shared-counterpart rule has passed before the first write.
    rows = [state['transactions'][change['id']] for change in result['changes']]
    if 'metadata' in write:
        write['metadata']['formatVersion'] = max(write['metadata']['formatVersion'], 5)
    store.db.executemany(
        'UPDATE transactions SET transfer_peer_id=?,is_transfer=?,reviewed=?,category_id=?,category_agent=?,category_edited=? '
        'WHERE workspace=? AND id=?',
        [(row.get('transferPeerId'), int(row['isTransfer']), int(row['reviewed']), row.get('categoryId'),
          row.get('categoryAgent'), int(row.get('categoryEdited', False)), workspace, row['id']) for row in rows],
    )
    write.update(dirty=True, workspace=workspace, history_operation=operation)
