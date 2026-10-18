"""Account metadata and history checks without materializing the saved ledger."""

from whab.plugins import StoreError


def account_overview(store, workspace, month, currency):
    """Read metadata and one review count from the same SQLite snapshot."""
    with store._snapshot():
        store._require_workspace(workspace)
        accounts = store._read_accounts(workspace)
        categories = store.categories(workspace)
        count = store.db.execute(
            "SELECT COUNT(*) FROM transactions t JOIN accounts a "
            "ON a.workspace=t.workspace AND a.id=t.account_id "
            "WHERE t.workspace=? AND a.currency=? AND t.date>=? AND t.date<=? "
            "AND t.deleted=0 AND t.pending=0 AND t.reviewed=0",
            (workspace, currency, month + "-01", month + "-31"),
        ).fetchone()[0]
        return {"accounts": accounts, "categories": categories, "toReview": count}


def account_settings(store, workspace, account_id):
    """Historical records, including removed transactions, prevent currency changes."""
    with store._snapshot():
        store._require_workspace(workspace)
        account = next((row for row in store._read_accounts(workspace) if row["id"] == account_id), None)
        if account is None:
            raise StoreError("Account not found.")
        has_history = store.db.execute(
            "SELECT EXISTS(SELECT 1 FROM transactions WHERE workspace=? AND account_id=?)",
            (workspace, account_id),
        ).fetchone()[0]
        return {"account": account, "currencyEditable": not bool(has_history)}
