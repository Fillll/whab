"""Detached portable records without materializing a second domain ledger."""

import json

from whab.domain.portable import ACCOUNT_FIELDS
from whab.plugins import StoreError


def snapshot(store, workspace):
    """Read only public columns atomically; the caller must validate the result."""
    if not isinstance(workspace, str) or workspace not in {"demo", "personal"}:
        raise StoreError("Invalid workspace.")
    with store._snapshot():
        meta = store.db.execute("SELECT * FROM meta WHERE workspace=?", (workspace,)).fetchone()
        if meta is None:
            raise StoreError("Workspace not found.")
        accounts = [
            {**{key: record[key] for key in ACCOUNT_FIELDS},
             **{key: record[key] for key in ("bankWebsite", "nameOverridden") if key in record}}
            for record in store._read_accounts(workspace)
        ]
        categories = [dict(row) for row in store.db.execute(
            'SELECT id,name,group_name AS "group",target,currency FROM categories WHERE workspace=? ORDER BY rowid',
            (workspace,),
        )]
        transactions = []
        original_column = "original_details" in {row["name"] for row in store.db.execute("PRAGMA table_info(transactions)")}
        for row in store.db.execute(
            "SELECT id,account_id AS accountId,date,merchant,merchant_logo AS merchantLogo,amount,"
            "category_id AS categoryId,pending,reviewed,source,note,is_transfer AS isTransfer,"
            "inferred_transfer AS inferredTransfer,category_edited AS categoryEdited,deleted "
            + (",original_details " if original_column else "") +
            "FROM transactions WHERE workspace=? ORDER BY rowid", (workspace,),
        ):
            record = dict(row)
            original = record.pop("original_details", None)
            if original is not None:
                record["original"] = json.loads(original)
            for key in ("pending", "reviewed", "isTransfer", "inferredTransfer", "categoryEdited", "deleted"):
                record[key] = bool(record[key])
            transactions.append(record)
        budget = {}
        for row in store.db.execute("SELECT * FROM budget WHERE workspace=?", (workspace,)):
            budget.setdefault(row["month"], {})[row["category_id"]] = row["assigned"]
        funding = []
        if store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='future_funding'").fetchone():
            funding = [dict(row) for row in store.db.execute(
                "SELECT month,currency,assigned FROM future_funding WHERE workspace=? ORDER BY month,currency",
                (workspace,),
            )]
        modes = []
        if store.db.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name='rollover_modes'").fetchone():
            modes = [dict(row) for row in store.db.execute(
                "SELECT month,currency,mode FROM rollover_modes WHERE workspace=? ORDER BY month,currency",
                (workspace,),
            )]
        return {
            "formatVersion": 1, "budgetStartMonth": meta["budget_start_month"],
            "accounts": accounts, "categories": categories, "transactions": transactions,
            "assignments": [
                {"month": month, "categoryId": category, "assigned": assigned}
                for month, rows in budget.items() for category, assigned in rows.items()
            ],
            "futureFunding": funding, "rolloverModes": modes,
        }
