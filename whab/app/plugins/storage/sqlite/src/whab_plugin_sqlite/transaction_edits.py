"""Atomic annotation edits without reading or rewriting unrelated ledger history."""

from whab.domain import commands
from whab.plugins import UNSET, StoreError


def edit_transaction(
    store, workspace: str, transaction_id: str, category_id=UNSET,
    reviewed: bool | None = None, note=UNSET,
) -> None:
    with store._lock:
        store.db.execute("BEGIN IMMEDIATE")
        try:
            if not isinstance(workspace, str) or workspace not in {"demo", "personal"}:
                raise StoreError("Invalid workspace.")
            if not store.db.execute("SELECT 1 FROM meta WHERE workspace=?", (workspace,)).fetchone():
                raise StoreError("Workspace not found.")
            state = {
                "accounts": {row["id"]: row for row in store._read_accounts(workspace)},
                "categories": {
                    row["id"]: {
                        "id": row["id"], "name": row["name"], "group": row["group_name"],
                        "target": row["target"], "currency": row["currency"],
                    }
                    for row in store.db.execute("SELECT * FROM categories WHERE workspace=?", (workspace,))
                },
                "transactions": {},
            }
            # Preserve the core's missing-ID and unhashable-ID errors rather than
            # allowing SQLite's text affinity to match numeric IDs to text keys.
            if isinstance(transaction_id, str):
                row = store.db.execute(
                    "SELECT id,account_id AS accountId,category_id AS categoryId,reviewed,note,"
                    "is_transfer AS isTransfer,inferred_transfer AS inferredTransfer,"
                    "category_edited AS categoryEdited,deleted "
                    "FROM transactions WHERE workspace=? AND id=?", (workspace, transaction_id),
                ).fetchone()
                if row is not None:
                    state["transactions"][row["id"]] = {
                        **dict(row), **{key: bool(row[key]) for key in (
                            "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted",
                        )},
                    }
            commands.edit_transaction(state, transaction_id, category_id, reviewed, note)
            transaction = state["transactions"][transaction_id]
            store.db.execute(
                "UPDATE transactions SET category_id=?,reviewed=?,note=?,is_transfer=?,category_edited=? "
                "WHERE workspace=? AND id=?",
                (transaction["categoryId"], int(transaction["reviewed"]), transaction["note"],
                 int(transaction["isTransfer"]), int(transaction["categoryEdited"]), workspace, transaction_id),
            )
            store.db.execute("COMMIT")
        except Exception:
            store.db.execute("ROLLBACK")
            raise
