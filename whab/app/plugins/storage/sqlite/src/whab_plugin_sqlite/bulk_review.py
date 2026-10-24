"""Atomic review flags over a bounded selection, with core-owned validation."""

from whab.domain import commands
from whab.plugins import validate_review_batch


def review_transactions(store, workspace, transaction_ids, reviewed):
    with store._authority_write() as transaction:
        try:
            store._require_workspace(workspace)
            selected = validate_review_batch(transaction_ids, reviewed)
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
            placeholders = ",".join("?" for _ in selected)
            for row in store.db.execute(
                "SELECT id,account_id AS accountId,category_id AS categoryId,reviewed,note,"
                "is_transfer AS isTransfer,inferred_transfer AS inferredTransfer,"
                "category_edited AS categoryEdited,deleted "
                "FROM transactions WHERE workspace=? AND id IN (" + placeholders + ")",
                [workspace, *selected],
            ):
                state["transactions"][row["id"]] = {
                    **dict(row), **{key: bool(row[key]) for key in (
                        "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted",
                    )},
                }
            # Validate every target in caller order before any database change.
            for transaction_id in selected:
                commands.edit_transaction(state, transaction_id, reviewed=reviewed)
            store.db.execute(
                "UPDATE transactions SET reviewed=? WHERE workspace=? AND id IN (" + placeholders + ")",
                [int(reviewed), workspace, *selected],
            )
            transaction["dirty"] = True
        except BaseException:
            raise
