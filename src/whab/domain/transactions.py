"""Public transaction projections without internal mutation metadata."""

UNSET = object()


def public_transaction(transaction: dict) -> dict:
    return {
        key: transaction.get(key)
        for key in (
            "id",
            "accountId",
            "date",
            "merchant",
            "amount",
            "categoryId",
            "pending",
            "reviewed",
            "source",
            "note",
            "isTransfer",
            "inferredTransfer",
        )
    }


def transaction_rows(state: dict, month: str | None = None) -> list[dict]:
    rows = [
        public_transaction(transaction)
        for transaction in state["transactions"].values()
        if not transaction.get("deleted") and (not month or transaction["date"].startswith(month))
    ]
    return sorted(
        rows, key=lambda transaction: (transaction["date"], transaction["id"]), reverse=True
    )
