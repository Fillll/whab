"""SQLite-owned private images, committed with their transaction and portable ledger."""

from whab.plugins import StoreError, receipt_media_type


def transaction_exists(store, workspace, transaction_id):
    """Check one active identity without loading register history or receipt data."""
    with store._snapshot():
        store._require_workspace(workspace)
        if not isinstance(transaction_id, str):
            return False
        return bool(store.db.execute(
            "SELECT 1 FROM transactions WHERE workspace=? AND id=? AND deleted=0",
            (workspace, transaction_id),
        ).fetchone())


def check_transaction(store, workspace, transaction_id):
    if type(workspace) is not str or workspace not in {"personal", "demo"} or not store.db.execute(
        "SELECT 1 FROM transactions WHERE workspace=? AND id=? AND deleted=0",
        (workspace, transaction_id),
    ).fetchone():
        raise StoreError("Transaction not found.")


def ids(store, workspace):
    if type(workspace) is not str or workspace not in {"personal", "demo"}:
        raise StoreError("Invalid workspace.")
    with store._lock:
        return [row[0] for row in store.db.execute(
            "SELECT r.transaction_id FROM transaction_receipts r JOIN transactions t ON t.workspace=r.workspace AND t.id=r.transaction_id WHERE r.workspace=? AND t.deleted=0 ORDER BY r.transaction_id",
            (workspace,),
        )]


def get(store, workspace, transaction_id):
    with store._lock:
        check_transaction(store, workspace, transaction_id)
        row = store.db.execute(
            "SELECT image FROM transaction_receipts WHERE workspace=? AND transaction_id=?",
            (workspace, transaction_id),
        ).fetchone()
        return row[0] if row else None


def save(store, workspace, transaction_id, raw):
    receipt_media_type(raw)
    with store._lock:
        check_transaction(store, workspace, transaction_id)
        store.db.execute("SAVEPOINT save_receipt_image")
        try:
            store.db.execute(
                "INSERT INTO transaction_receipts(workspace,transaction_id,image) VALUES(?,?,?) ON CONFLICT(workspace,transaction_id) DO UPDATE SET image=excluded.image",
                (workspace, transaction_id, raw),
            )
            # A successful upload must remain portable. The sanitized export
            # validates aggregate image/database size before this write commits.
            from .portable import export_files

            export_files(store, workspace)
            store.db.execute("RELEASE save_receipt_image")
        except BaseException:
            store.db.execute("ROLLBACK TO save_receipt_image")
            store.db.execute("RELEASE save_receipt_image")
            raise


def delete(store, workspace, transaction_id):
    with store._lock:
        check_transaction(store, workspace, transaction_id)
        store.db.execute("DELETE FROM transaction_receipts WHERE workspace=? AND transaction_id=?", (workspace, transaction_id))
