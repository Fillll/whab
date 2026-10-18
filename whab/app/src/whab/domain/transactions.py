"""Public transaction projections and bounded review-only annotation validation."""

from whab.domain.errors import StoreError

UNSET = object()


def validate_review_batch(transaction_ids, reviewed) -> tuple[str, ...]:
    """Validate one bounded, review-only intent without changing its target order."""
    if type(transaction_ids) not in (list, tuple) or not 1 <= len(transaction_ids) <= 100:
        raise StoreError("Review batch must contain between 1 and 100 transaction IDs.")
    if type(reviewed) is not bool:
        raise StoreError("Invalid review status.")
    if any(type(value) is not str or not 1 <= len(value) <= 200 or "\x00" in value
           for value in transaction_ids):
        raise StoreError("Invalid transaction ID.")
    if len(set(transaction_ids)) != len(transaction_ids):
        raise StoreError("Review batch transaction IDs must be distinct.")
    return tuple(transaction_ids)


def public_transaction(transaction: dict) -> dict:
    return {
        key: transaction.get(key)
        for key in (
            "id",
            "accountId",
            "date",
            "merchant",
            "merchantLogo",
            "amount",
            "categoryId",
            "pending",
            "reviewed",
            "source",
            "note",
            "isTransfer",
            "inferredTransfer",
            "currency",
        )
    }


def transaction_rows(state: dict, month: str | None = None) -> list[dict]:
    rows = [
        public_transaction(
            {
                **transaction,
                "currency": state["accounts"][transaction["accountId"]].get("currency", "USD"),
            }
        )
        for transaction in state["transactions"].values()
        if not transaction.get("deleted") and (not month or transaction["date"].startswith(month))
    ]
    return sorted(
        rows, key=lambda transaction: (transaction["date"], transaction["id"]), reverse=True
    )
