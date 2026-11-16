"""Persist reversible tombstones and manual balance effects in one native commit."""

from whab.domain.transaction_voids import plan, validate_void_ids

from .transfer_pairs import context


def retains_voids(state):
    """Fence old readers even when only reversal history retains void intent."""
    journal = state.get('history') or {}
    transactions = state['transactions']
    rows = transactions.values() if isinstance(transactions, dict) else transactions
    return (any('voided' in row for row in rows)
            or any('voided' in row for row in journal.get('baseline', {}).get('transactions', ()))
            or any(row is not None and 'voided' in row for entry in journal.get('entries', ())
                   for change in entry['changes'] if change['path'][0] == 'transactions'
                   for row in (change['before'], change['after'])))


def preview(store, workspace, transaction_ids, action):
    identifiers = validate_void_ids(transaction_ids)
    with store._snapshot():
        return plan(context(store, workspace, identifiers), identifiers, action=action)


def apply(store, workspace, transaction_ids, action, fingerprint):
    identifiers = validate_void_ids(transaction_ids)
    with store._authority_write(workspace=workspace, transaction_ids=identifiers) as write:
        state = context(store, workspace, identifiers)
        result = plan(state, identifiers, action=action, fingerprint=fingerprint, apply=True)
        # The complete plan, including all counterpart and balance checks, passes
        # before the first SQL mutation. Financial/source fields remain verbatim.
        store.db.executemany(
            'UPDATE transactions SET deleted=?,reviewed=?,voided=?,provider_deleted=? WHERE workspace=? AND id=?',
            [(int(row['deleted']), int(row['reviewed']), int(row['voided']) if 'voided' in row else None,
              int(row.get('providerDeleted', False)), workspace, row['id'])
             for row in (state['transactions'][key] for key in identifiers)],
        )
        store.db.executemany('UPDATE accounts SET balance=? WHERE workspace=? AND id=?', [
            (change['after'], workspace, change['accountId']) for change in result['balanceChanges']
            if change['before'] != change['after']
        ])
        if 'metadata' in write:
            write['metadata']['formatVersion'] = max(write['metadata']['formatVersion'], 6)
        write.update(dirty=True, workspace=workspace, history_operation=action + '_transactions')
        return result
