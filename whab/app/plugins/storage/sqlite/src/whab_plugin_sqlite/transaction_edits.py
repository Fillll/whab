"""Atomic annotation edits without reading or rewriting unrelated ledger history."""

import json

from whab.domain import commands
from whab.plugins import UNSET, StoreError


def edit_transaction(
    store, workspace: str, transaction_id: str, category_id=UNSET,
    reviewed: bool | None = None, note=UNSET, *, details=None,
) -> None:
    with store._authority_write() as write:
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
                    "category_edited AS categoryEdited,deleted, date,merchant,amount,source,original_details "
                    "FROM transactions WHERE workspace=? AND id=?", (workspace, transaction_id),
                ).fetchone()
                if row is not None:
                    state["transactions"][row["id"]] = {
                        **dict(row), **{key: bool(row[key]) for key in (
                            "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted",
                        )},
                    }
            if details is not None:
                transaction = state["transactions"].get(transaction_id)
                if transaction is not None:
                    original = transaction.pop("original_details")
                    if original is not None:
                        transaction["original"] = json.loads(original)
                balances = {key: row["balance"] for key, row in state["accounts"].items()}
                commands.edit_transaction_details(state, transaction_id, details)
                transaction = state["transactions"][transaction_id]
                store.db.execute(
                    "UPDATE transactions SET account_id=?,date=?,merchant=?,amount=?,original_details=? WHERE workspace=? AND id=?",
                    (transaction["accountId"], transaction["date"], transaction["merchant"], transaction["amount"],
                     json.dumps(transaction["original"]) if "original" in transaction else None, workspace, transaction_id),
                )
                for key, account in state["accounts"].items():
                    if account["balance"] != balances[key]:
                        store.db.execute("UPDATE accounts SET balance=? WHERE workspace=? AND id=?",
                                         (account["balance"], workspace, key))
            else:
                commands.edit_transaction(state, transaction_id, category_id, reviewed, note)
                transaction = state["transactions"][transaction_id]
            store.db.execute(
                "UPDATE transactions SET category_id=?,reviewed=?,note=?,is_transfer=?,category_edited=? "
                "WHERE workspace=? AND id=?",
                (transaction["categoryId"], int(transaction["reviewed"]), transaction["note"],
                 int(transaction["isTransfer"]), int(transaction["categoryEdited"]), workspace, transaction_id),
            )
            write["dirty"] = True
        except Exception:
            raise
