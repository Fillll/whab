"""One native transaction for a bounded core-validated edit plan."""

from whab.domain.bulk import bulk_changes, validate_edits


def selection(store, workspace, operation, edits):
    store._require_workspace(workspace)
    selected = validate_edits(operation, edits)
    identifiers = [row['transactionId'] for row in selected] if operation == 'edit_transactions' else ()
    # The existing native reader preserves raw annotations and original metadata.
    state = store._read_state(workspace, transaction_ids=identifiers, include_history=False)
    if operation == 'classify_accounts':
        from whab.domain.accounts import is_on_budget
        changed = [row['accountId'] for row in selected if 'onBudget' in row
                   and row['accountId'] in state['accounts']
                   and row['onBudget'] != is_on_budget(state['accounts'][row['accountId']])]
        if changed:
            matched = [row[0] for row in store.db.execute(
                'SELECT id FROM transactions WHERE workspace=? AND transfer_peer_id IS NOT NULL AND account_id IN (' + ','.join('?' for _ in changed) + ')',
                (workspace, *changed))]
            if matched:
                state = store._read_state(workspace, transaction_ids=matched, include_history=False)
    return state, selected


def preview(store, workspace, operation, edits):
    with store._snapshot():
        state, selected = selection(store, workspace, operation, edits)
        return bulk_changes(state, operation, selected)


def apply(store, workspace, operation, edits):
    edits = validate_edits(operation, edits)
    identifiers = [row['transactionId'] for row in edits] if operation == 'edit_transactions' else ()
    with store._authority_write(workspace=workspace, transaction_ids=identifiers) as write:
        state, selected = selection(store, workspace, operation, edits)
        result = bulk_changes(state, operation, selected, apply=True)
        # No SQL writes occur until EVERY target and change has passed core rules.
        if operation == 'edit_transactions':
            store.db.executemany('UPDATE transactions SET category_id=?,category_agent=?,reviewed=?,note=?,is_transfer=?,category_edited=? WHERE workspace=? AND id=?', [
                (row['categoryId'], row.get('categoryAgent'), int(row['reviewed']), row['note'], int(row['isTransfer']), int(row['categoryEdited']), workspace, identifier)
                for identifier, row in state['transactions'].items()
            ])
        else:
            store.db.executemany('UPDATE accounts SET type=?,on_budget=?,subtype=? WHERE workspace=? AND id=?', [
                (row['after']['type'], int(row['after']['onBudget']), row['after'].get('subtype'), workspace, row['id']) for row in result['changes']
            ])
        write.update(dirty=True, workspace=workspace, history_operation=operation)
        return result
