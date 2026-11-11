"""Disposable, revision-bound Unicode merchant index outside the native ledger."""

from sqlite3 import Error, OperationalError, connect
from weakref import finalize


class MerchantIndex:
    def __init__(self, rows, stamp):
        self.db = connect(':memory:', check_same_thread=False)
        self._close = finalize(self, self.db.close)
        try:
            self.db.execute('CREATE TABLE merchants(workspace TEXT,transaction_id TEXT,folded TEXT,UNIQUE(workspace,transaction_id))')
            try:
                self.db.execute("CREATE VIRTUAL TABLE terms USING fts5(folded,content='merchants',content_rowid='rowid',tokenize='trigram case_sensitive 1')")
                self.fts = True
            except OperationalError as error:
                if 'no such module: fts5' not in str(error):
                    raise
                self.fts = False
            self.db.executemany('INSERT INTO merchants VALUES(?,?,?)',
                                ((workspace, identifier, merchant.casefold()) for workspace, identifier, merchant in rows))
            if self.fts:
                self.db.execute("INSERT INTO terms(terms) VALUES('rebuild')")
            self.db.commit()
            self.stamp = stamp
        except BaseException:
            self.close()
            raise

    def identifiers(self, workspace, merchants):
        identifiers = set()
        for merchant in merchants:
            needle = merchant.casefold()
            if self.fts and len(needle) >= 3:
                phrase = '"' + needle.replace('"', '""') + '"'
                rows = self.db.execute('SELECT m.transaction_id FROM terms JOIN merchants m ON m.rowid=terms.rowid '
                                       'WHERE terms MATCH ? AND m.workspace=? AND instr(m.folded,?)>0',
                                       (phrase, workspace, needle))
            else:
                rows = self.db.execute('SELECT transaction_id FROM merchants WHERE workspace=? AND instr(folded,?)>0', (workspace, needle))
            identifiers.update(row[0] for row in rows)
        return list(identifiers)

    def update(self, workspace, identifiers, rows):
        replacements = {identifier: merchant.casefold() for identifier, merchant in rows}
        with self.db:
            for identifier in identifiers:
                old = self.db.execute('SELECT rowid,folded FROM merchants WHERE workspace=? AND transaction_id=?', (workspace, identifier)).fetchone()
                new = replacements.get(identifier)
                if old is not None and old[1] == new:
                    continue
                if old is not None:
                    if self.fts:
                        self.db.execute("INSERT INTO terms(terms,rowid,folded) VALUES('delete',?,?)", old)
                    self.db.execute('DELETE FROM merchants WHERE rowid=?', (old[0],))
                if new is not None:
                    rowid = self.db.execute('INSERT INTO merchants VALUES(?,?,?)', (workspace, identifier, new)).lastrowid
                    if self.fts:
                        self.db.execute('INSERT INTO terms(rowid,folded) VALUES(?,?)', (rowid, new))

    def close(self):
        self._close()


def selection(store, workspace, merchants):
    stamp = (store.db.total_changes, store.db.execute('PRAGMA data_version').fetchone()[0])
    cached = getattr(store, '_merchant_index', None)
    if cached is None or cached.stamp != stamp:
        replacement = MerchantIndex(store.db.execute('SELECT workspace,id,merchant FROM transactions'), stamp)
        store._merchant_index = replacement
        if cached is not None:
            cached.close()
        cached = replacement
    return cached.identifiers(workspace, merchants)


def promote(store, selection, before, after):
    cached = getattr(store, '_merchant_index', None)
    if cached is None or selection is None or cached.stamp != before or before[1] != after[1]:
        return
    workspace, identifiers = selection
    try:
        rows = store.db.execute('SELECT id,merchant FROM transactions WHERE workspace=? AND id IN (' + ','.join('?' for _ in identifiers) + ')', (workspace, *identifiers))
        cached.update(workspace, identifiers, rows)
        cached.stamp = after
    except Error:
        # Index maintenance cannot turn an already-durable commit into a failure.
        cached.close()
        store._merchant_index = None
