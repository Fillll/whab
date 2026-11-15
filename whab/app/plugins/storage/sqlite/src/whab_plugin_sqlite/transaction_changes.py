"""Persist a complete core-validated edit/create plan in one native transaction."""

import json

from whab.domain.transaction_changes import transaction_changes, validate_transaction_changes


def _selection(store, workspace, edits, creates, preserve_balances):
    store._require_workspace(workspace)
    edits, creates = validate_transaction_changes(edits, creates, preserve_balances=preserve_balances)
    identifiers = [row['transactionId'] for row in edits] + [row['creationId'] for row in creates]
    # Include proposed identities so core rejects a collision, including tombstones.
    state = store._read_state(workspace, transaction_ids=identifiers, include_history=False)
    return state, edits, creates


def preview(store, workspace, edits, creates, *, preserve_balances=False):
    with store._snapshot():
        state, edits, creates = _selection(store, workspace, edits, creates, preserve_balances)
        return transaction_changes(state, edits, creates, preserve_balances=preserve_balances)


def apply(store, workspace, edits, creates, *, preserve_balances=False):
    edits, creates = validate_transaction_changes(edits, creates, preserve_balances=preserve_balances)
    identifiers = [row['transactionId'] for row in edits] + [row['creationId'] for row in creates]
    with store._authority_write(workspace=workspace, transaction_ids=identifiers) as write:
        state, edits, creates = _selection(store, workspace, edits, creates, preserve_balances)
        balances = {key: row['balance'] for key, row in state['accounts'].items()}
        result = transaction_changes(state, edits, creates, preserve_balances=preserve_balances, apply=True)
        # All validation completes before any SQL. The conflict branch updates only
        # existing validated edits; proposed creates were checked absent above.
        store.db.executemany(
            'INSERT INTO transactions(id,workspace,account_id,date,merchant,amount,category_id,pending,reviewed,source,note,is_transfer,category_edited,deleted,inferred_transfer,merchant_logo,original_details,category_agent,transfer_peer_id) '
            'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace,id) DO UPDATE SET '
            'account_id=excluded.account_id,date=excluded.date,merchant=excluded.merchant,amount=excluded.amount,'
            'category_id=excluded.category_id,pending=excluded.pending,reviewed=excluded.reviewed,source=excluded.source,'
            'note=excluded.note,is_transfer=excluded.is_transfer,category_edited=excluded.category_edited,'
            'deleted=excluded.deleted,inferred_transfer=excluded.inferred_transfer,merchant_logo=excluded.merchant_logo,'
            'original_details=excluded.original_details,category_agent=excluded.category_agent,transfer_peer_id=excluded.transfer_peer_id',
            [(row['id'], workspace, row['accountId'], row['date'], row['merchant'], row['amount'],
              row.get('categoryId'), int(row.get('pending', False)), int(row.get('reviewed', False)),
              row['source'], row.get('note', ''), int(row.get('isTransfer', False)),
              int(row.get('categoryEdited', False)), int(row.get('deleted', False)),
              int(row.get('inferredTransfer', False)), row.get('merchantLogo'),
              json.dumps(row['original']) if 'original' in row else None,
              row.get('categoryAgent'), row.get('transferPeerId')) for row in state['transactions'].values()],
        )
        store.db.executemany('UPDATE accounts SET balance=? WHERE workspace=? AND id=?', [
            (row['balance'], workspace, key) for key, row in state['accounts'].items()
            if row['balance'] != balances[key]
        ])
        # Existing-only edits can reuse bounded audit reads. New identities trigger
        # the authority's full-history fallback so undo removes the created rows too.
        write.update(dirty=True, workspace=workspace, history_operation='change_transactions')
        return result
