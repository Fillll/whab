"""Explicit allowlist between the ledger's private state and the public plugin API."""

from whab.plugins import AccountSnapshot, BudgetSnapshot, CategorySnapshot, TransactionSnapshot


def build_snapshot(workspace, data):
    budget = {row["categoryId"]: row for row in data["budget"]}
    return BudgetSnapshot(
        workspace=workspace,
        month=data["month"],
        accounts=tuple(
            AccountSnapshot(
                id=a["id"],
                name=a["name"],
                type=a["type"],
                subtype=a.get("subtype") or "",
                balance_cents=a["balance"],
                stale=bool(a.get("stale")),
                balance_unknown=bool(a.get("balanceUnknown")),
            )
            for a in data["accounts"]
        ),
        categories=tuple(
            CategorySnapshot(
                id=c["id"],
                name=c["name"],
                group=c["group"],
                assigned_cents=budget[c["id"]]["assigned"],
                activity_cents=budget[c["id"]]["spent"],
                available_cents=budget[c["id"]]["available"],
            )
            for c in data["categories"]
        ),
        transactions=tuple(
            TransactionSnapshot(
                id=t["id"],
                date=t["date"],
                payee=t["merchant"],
                amount_cents=t["amount"],
                account_id=t["accountId"],
                category_id=t["categoryId"],
                pending=t["pending"],
                is_transfer=bool(t.get("isTransfer")),
                reviewed=t["reviewed"],
            )
            for t in data["transactions"]
        ),
    )
