"""Append an audited entry without rewriting its immutable JSON baseline."""

import json

_RESERVE_BYTES = 64 * 1024


def save_history(store, workspace, journal, previous, *, cache_valid):
    hints = getattr(store, '_history_blob_positions', {})
    hint = hints.get(workspace)
    entries = journal['entries']
    before = previous['entries'] if previous else []
    append_only = (previous is not None and journal['baseline'] is previous['baseline']
                   and len(entries) == len(before) + 1 and entries[:-1] == before)
    can_append = (cache_valid and hint is not None and append_only
                  and hint[3] == (before[-1]['hash'] if before else None)
                  and callable(getattr(store.db, 'blobopen', None))
                  and not store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='trigger' LIMIT 1").fetchone())
    if can_append:
        rowid, end, capacity, _ = hint
        addition = (b',' if before else b'') + json.dumps(entries[-1], ensure_ascii=True, separators=(',', ':')).encode()
        if end + len(addition) + 2 <= capacity:
            with store.db.blobopen('budget_history', 'record', rowid, readonly=False) as blob:
                if len(blob) == capacity:
                    blob.seek(end)
                    blob.write(addition + b']}')
                    hints[workspace] = (rowid, end + len(addition), capacity, entries[-1]['hash'])
                    store._evidence_stamp = None
                    return
    raw = json.dumps(journal, ensure_ascii=True, separators=(',', ':'))
    end = len(raw.encode()) - 2
    # The record remains ordinary JSON TEXT for old readers. Whitespace reserves
    # space for SQLite's transactional incremental blob writes, with no schema change.
    reserve = min(_RESERVE_BYTES, max(4096, (end + 2) // 16))
    padded = raw + ' ' * reserve
    store.db.execute("INSERT INTO budget_history(workspace,record) VALUES(?,?) ON CONFLICT(workspace) DO UPDATE SET record=excluded.record",
                     (workspace, padded))
    rowid = store.db.execute('SELECT rowid FROM budget_history WHERE workspace=?', (workspace,)).fetchone()[0]
    hints[workspace] = (rowid, end, end + 2 + reserve, entries[-1]['hash'] if entries else None)
    store._history_blob_positions = hints
