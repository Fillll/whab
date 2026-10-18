"""Private, request-local inputs for the core workspace projection functions.

Every transaction retains its financial and reference fields, including deleted,
pending, transfer and off-budget history. Core owns financial rules and arithmetic.
Only requested months need the additional public display fields: core never
publishes historical transactions outside those months. This partial-row contract
must be kept in step with the fields consumed by core's workspace calculations.

This is not a replacement for Store.state: mutation, export and private connection
reads continue to use their existing complete records.
"""

from whab.domain.dates import shift_month
from whab.domain.errors import StoreError
from whab.domain.validation import validate_month


def snapshot(store, workspace: str, month: str, count: int = 1) -> dict:
    """Read coherent projection inputs without copying historical display text."""
    if not isinstance(workspace, str) or workspace not in {"demo", "personal"}:
        raise StoreError("Invalid workspace.")
    with store._snapshot():
        meta = store.db.execute("SELECT * FROM meta WHERE workspace=?", (workspace,)).fetchone()
        if meta is None:
            raise StoreError("Workspace not found.")
        state = {
            "_id": workspace,
            "budgetStartMonth": meta["budget_start_month"],
            "accounts": {row["id"]: row for row in store._read_accounts(workspace)},
            "categories": {}, "transactions": {}, "budget": {},
            "futureFunding": {}, "rolloverModes": {},
        }
        for row in store.db.execute("SELECT * FROM categories WHERE workspace=? ORDER BY rowid", (workspace,)):
            state["categories"][row["id"]] = {
                "id": row["id"], "name": row["name"], "group": row["group_name"],
                "target": row["target"], "currency": row["currency"],
            }
        for row in store.db.execute(
            "SELECT id,account_id,date,amount,category_id,pending,is_transfer,deleted "
            "FROM transactions WHERE workspace=? ORDER BY rowid", (workspace,),
        ):
            state["transactions"][row["id"]] = {
                "id": row["id"], "accountId": row["account_id"], "date": row["date"],
                "amount": row["amount"], "categoryId": row["category_id"],
                "pending": bool(row["pending"]), "isTransfer": bool(row["is_transfer"]),
                "deleted": bool(row["deleted"]),
            }
        for row in store.db.execute("SELECT * FROM budget WHERE workspace=?", (workspace,)):
            state["budget"].setdefault(row["month"], {})[row["category_id"]] = row["assigned"]
        # Older format-1 payloads may omit these additive planning tables.
        if store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='future_funding'").fetchone():
            for row in store.db.execute("SELECT * FROM future_funding WHERE workspace=?", (workspace,)):
                state["futureFunding"].setdefault(row["month"], {})[row["currency"]] = row["assigned"]
        if store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='rollover_modes'").fetchone():
            for row in store.db.execute("SELECT * FROM rollover_modes WHERE workspace=?", (workspace,)):
                state["rolloverModes"].setdefault(row["month"], {})[row["currency"]] = row["mode"]

        # Preserve legacy read errors (notably workspace/account metadata) before
        # request errors. Category/reference validation still happens later in core.
        validate_month(month)
        if type(count) is not int or not 1 <= count <= 12:
            raise StoreError("Period count must be an integer between 1 and 12.")
        for offset in range(count):
            validate_month(shift_month(month, offset))
        # Prefix bounds match core's date[:7] grouping, including unusual suffixes.
        # The exclusive upper bound may be 2300-01 for the final supported month.
        following = shift_month(month, count)
        for row in store.db.execute(
            "SELECT id,merchant,merchant_logo,reviewed,source,note,inferred_transfer "
            "FROM transactions WHERE workspace=? AND date>=? AND date<?", (workspace, month, following),
        ):
            state["transactions"][row["id"]].update(
                merchant=row["merchant"], merchantLogo=row["merchant_logo"], reviewed=bool(row["reviewed"]),
                source=row["source"], note=row["note"], inferredTransfer=bool(row["inferred_transfer"]),
            )
        return state
